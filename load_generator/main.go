package main

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"math/rand"
	"net"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/aws/aws-lambda-go/lambda"
)

// BenchmarkRequest represents the payload Python sends to Go
type BenchmarkRequest struct {
	SubmissionID string `json:"submission_id"`
	Endpoint     string `json:"endpoint"`
	Concurrency  int    `json:"concurrency"`
	DurationSec  int    `json:"duration_seconds"`
}

type BenchmarkResult struct {
	SubmissionID string            `json:"submission_id"`
	Total        uint64            `json:"total_requests"`
	Success      uint64            `json:"success"`
	Failures     uint64            `json:"failures"`
	TPS          float64           `json:"tps"`
	ErrorRate    float64           `json:"error_rate"`
	AvgLatencyMs float64           `json:"avg_latency_ms"`
	MinLatencyMs float64           `json:"min_latency_ms"`
	MaxLatencyMs float64           `json:"max_latency_ms"`
	P50LatencyMs float64           `json:"p50_latency_ms"`
	P90LatencyMs float64           `json:"p90_latency_ms"`
	P99LatencyMs float64           `json:"p99_latency_ms"`
	StatusCodes  map[int]uint64    `json:"status_codes"`
	ErrorTypes   map[string]uint64 `json:"error_types"`
}

// Global, optimized HTTP client. Reuses TCP handshakes.
// This prevents running out of local ephemeral sockets (EMFILE / TIME_WAIT states).
var httpClient = &http.Client{
	Transport: &http.Transport{
		MaxIdleConns:        20000,
		MaxIdleConnsPerHost: 5000,
		IdleConnTimeout:     90 * time.Second,
	},
	Timeout: 5 * time.Second,
}

// Lambda handler function
func lambdaHandler(ctx context.Context, req BenchmarkRequest) (BenchmarkResult, error) {
	if req.Endpoint == "" || req.Concurrency <= 0 || req.DurationSec <= 0 {
		return BenchmarkResult{}, errors.New("invalid request parameters")
	}
	result := startStressTest(req.SubmissionID, req.Endpoint, req.Concurrency, time.Duration(req.DurationSec)*time.Second)
	return result, nil
}

func main() {
	if os.Getenv("AWS_LAMBDA_RUNTIME_API") != "" {
		// Run in AWS Lambda mode
		lambda.Start(lambdaHandler)
	} else {
		// Run in Local HTTP server mode
		http.HandleFunc("/benchmark", handleBenchmark)
		log.Println("Go Load Generator listening on :8001 (Local Mode)...")
		log.Fatal(http.ListenAndServe(":8001", nil))
	}
}

func incrementError(m *sync.Map, key string) {
	value, _ := m.LoadOrStore(key, new(uint64))
	atomic.AddUint64(value.(*uint64), 1)
}

func handleBenchmark(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req BenchmarkRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if req.Endpoint == "" {
		http.Error(w, "endpoint is required", http.StatusBadRequest)
		return
	}

	if req.Concurrency <= 0 {
		http.Error(w, "concurrency must be > 0", http.StatusBadRequest)
		return
	}

	if req.DurationSec <= 0 {
		http.Error(w, "duration_seconds must be > 0", http.StatusBadRequest)
		return
	}

	// Spin off the load testing in a non-blocking background Goroutine
	result := startStressTest(req.SubmissionID, req.Endpoint, req.Concurrency, time.Duration(req.DurationSec)*time.Second)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(result)
}

// fastUUID generates a non-cryptographic hex string for order_id.
// It is significantly faster than using google/uuid for high-throughput load generation.
func fastUUID(rng *rand.Rand) string {
	var b [16]byte
	for i := 0; i < 4; i++ {
		v := rng.Uint32()
		b[i*4] = byte(v)
		b[i*4+1] = byte(v >> 8)
		b[i*4+2] = byte(v >> 16)
		b[i*4+3] = byte(v >> 24)
	}
	return hex.EncodeToString(b[:])
}

// generateOrderJSON constructs the JSON payload into the provided slice with zero allocations.
// This supports the requirement for high concurrency and efficiency.
func generateOrderJSON(rng *rand.Rand, buf []byte) []byte {
	id := fastUUID(rng)

	p := rng.Float64()
	var orderType, side string
	hasPrice := false

	// Configurable probabilities: 40% LIMIT BUY, 40% LIMIT SELL, 10% MARKET BUY, 10% MARKET SELL
	if p < 0.40 {
		orderType = "LIMIT"
		side = "BUY"
		hasPrice = true
	} else if p < 0.80 {
		orderType = "LIMIT"
		side = "SELL"
		hasPrice = true
	} else if p < 0.90 {
		orderType = "MARKET"
		side = "BUY"
	} else {
		orderType = "MARKET"
		side = "SELL"
	}

	// Random quantity within range 1-100
	qty := 1 + rng.Intn(100)

	buf = append(buf, `{"order_id":"`...)
	buf = append(buf, id...)
	buf = append(buf, `","order_type":"`...)
	buf = append(buf, orderType...)
	buf = append(buf, `","side":"`...)
	buf = append(buf, side...)
	buf = append(buf, `","quantity":`...)
	buf = strconv.AppendInt(buf, int64(qty), 10)

	if hasPrice {
		// Realistic market behavior: mostly clustered around midpoint (100)
		// NormFloat64 gives N(0, 1). Multiply by stddev (3), add mean (100).
		price := math.Round(rng.NormFloat64()*3 + 100)

		// Range bounds: 90 to 110
		if price < 90 {
			price = 90
		} else if price > 110 {
			price = 110
		}

		buf = append(buf, `,"price":`...)
		buf = strconv.AppendFloat(buf, price, 'f', 2, 64)
	}

	buf = append(buf, '}')
	return buf
}

func startStressTest(subID, endpoint string, concurrency int, duration time.Duration) BenchmarkResult {
	// Implement warmup and steady-state phases
	warmupDuration := time.Duration(float64(duration) * 0.25)
	if warmupDuration < 1*time.Second {
		warmupDuration = 1 * time.Second
	}
	steadyDuration := duration - warmupDuration

	log.Printf("[%s] Target: %s | Bots: %d | Warmup: %s | Steady: %s", subID, endpoint, concurrency, warmupDuration, steadyDuration)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	var successCount uint64
	var failCount uint64
	var wg sync.WaitGroup
	var collectorWg sync.WaitGroup

	latencyChan := make(chan int64, 1000000)
	statusCodes := sync.Map{}
	errorCounts := sync.Map{}

	var latencies []int64

	collectorWg.Add(1)
	go func() {
		defer collectorWg.Done()
		for lat := range latencyChan {
			latencies = append(latencies, lat)
		}
	}()

	// State: 0 = warmup, 1 = paused (resetting), 2 = recording (steady state)
	state := int32(0)

	// Spawn the concurrent bot fleet
	for i := 0; i < concurrency; i++ {
		wg.Add(1)
		go func(botID int) {
			defer wg.Done()

			// Initialize bot-local RNG to avoid global lock contention (critical for high concurrency)
			rng := rand.New(rand.NewSource(time.Now().UnixNano() + int64(botID)))

			// Pre-allocate buffer for zero-allocation payload generation
			buf := make([]byte, 0, 256)

			for {
				select {
				case <-ctx.Done():
					return
				default:
				}

				currentState := atomic.LoadInt32(&state)
				if currentState == 1 {
					time.Sleep(10 * time.Millisecond)
					continue
				}

				// Generate randomized order payload efficiently
				buf = buf[:0]
				buf = generateOrderJSON(rng, buf)

				req, err := http.NewRequestWithContext(ctx, "POST", endpoint+"/order", bytes.NewReader(buf))
				if err != nil {
					if atomic.LoadInt32(&state) == 2 {
						incrementError(&errorCounts, "request_creation_failed")
						atomic.AddUint64(&failCount, 1)
					}
					continue
				}
				req.Header.Set("Content-Type", "application/json")

				requestStart := time.Now()
				resp, err := httpClient.Do(req)
				latencyNs := time.Since(requestStart).Nanoseconds()

				isRecording := atomic.LoadInt32(&state) == 2

				if err != nil {
					if isRecording {
						if errors.Is(err, context.Canceled) || ctx.Err() != nil {
							return
						}
						var netErr net.Error
						if errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &netErr) && netErr.Timeout()) {
							incrementError(&errorCounts, "timeout")
						} else if strings.Contains(err.Error(), "connection refused") {
							incrementError(&errorCounts, "connection_refused")
						} else {
							incrementError(&errorCounts, "network_error")
						}
						atomic.AddUint64(&failCount, 1)
					}
					continue
				}

				code := resp.StatusCode
				if isRecording {
					val, _ := statusCodes.LoadOrStore(code, new(uint64))
					atomic.AddUint64(val.(*uint64), 1)

					select {
					case latencyChan <- latencyNs:
					default:
						// If the channel is full, we drop the latency metric rather than blocking the bot,
						// maintaining the target throughput.
					}
				}

				if resp.StatusCode == http.StatusOK || resp.StatusCode == http.StatusCreated {
					_, _ = io.Copy(io.Discard, resp.Body)
					resp.Body.Close()
					if isRecording {
						atomic.AddUint64(&successCount, 1)
					}
				} else {
					_, _ = io.Copy(io.Discard, resp.Body)
					resp.Body.Close()

					if isRecording {
						if resp.StatusCode >= 400 && resp.StatusCode < 500 {
							incrementError(&errorCounts, fmt.Sprintf("http_%d", resp.StatusCode))
						} else if resp.StatusCode >= 500 {
							incrementError(&errorCounts, fmt.Sprintf("http_%d", resp.StatusCode))
						} else {
							incrementError(&errorCounts, "unexpected_status")
						}

						if atomic.LoadUint64(&failCount) < 10 {
							log.Printf("Bad response | status=%d", resp.StatusCode)
						}
						atomic.AddUint64(&failCount, 1)
					}
				}
			}
		}(i)
	}

	// Warmup phase (let bots establish connections and JIT warm up)
	time.Sleep(warmupDuration)

	// Pause bots for reset
	atomic.StoreInt32(&state, 1)

	// Wait a moment for in-flight warmup requests to complete
	time.Sleep(500 * time.Millisecond)

	// --- RESET ENDPOINT ---
	// Send a request to clean the orderbook after warmup.
	resetReq, err := http.NewRequestWithContext(ctx, "POST", endpoint+"/reset", nil)
	if err == nil {
		if resp, err := httpClient.Do(resetReq); err == nil {
			_, _ = io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			log.Printf("[%s] Reset orderbook after warmup (status: %d)", subID, resp.StatusCode)
		}
	}

	// Start steady-state recording phase
	atomic.StoreInt32(&state, 2)
	steadyStartTime := time.Now()

	// Steady-state phase
	time.Sleep(steadyDuration)

	// Trigger teardown
	cancel()
	wg.Wait()
	close(latencyChan)
	collectorWg.Wait()

	totalRequests := successCount + failCount
	elapsedSeconds := time.Since(steadyStartTime).Seconds()

	// Throughput calculation: T = N / t
	tps := float64(successCount) / elapsedSeconds

	sort.Slice(latencies, func(i, j int) bool {
		return latencies[i] < latencies[j]
	})

	var totalLatency int64
	for _, latency := range latencies {
		totalLatency += latency
	}

	avgLatencyMs := 0.0
	minLatencyMs := 0.0
	maxLatencyMs := 0.0

	if len(latencies) > 0 {
		avgLatencyMs = float64(totalLatency) / float64(len(latencies)) / 1e6
		minLatencyMs = float64(latencies[0]) / 1e6
		maxLatencyMs = float64(latencies[len(latencies)-1]) / 1e6
	}

	errorRate := 0.0
	if totalRequests > 0 {
		errorRate = float64(failCount) / float64(totalRequests) * 100
	}

	statusCodeMap := make(map[int]uint64)
	statusCodes.Range(func(key, value any) bool {
		statusCodeMap[key.(int)] = atomic.LoadUint64(value.(*uint64))
		return true
	})

	errorTypeMap := make(map[string]uint64)
	errorCounts.Range(func(key, value any) bool {
		errorTypeMap[key.(string)] = atomic.LoadUint64(value.(*uint64))
		return true
	})

	result := BenchmarkResult{
		SubmissionID: subID,
		Total:        totalRequests,
		Success:      successCount,
		Failures:     failCount,
		TPS:          tps,
		ErrorRate:    errorRate,
		AvgLatencyMs: avgLatencyMs,
		MinLatencyMs: minLatencyMs,
		MaxLatencyMs: maxLatencyMs,
		P50LatencyMs: percentile(latencies, 0.50),
		P90LatencyMs: percentile(latencies, 0.90),
		P99LatencyMs: percentile(latencies, 0.99),
		StatusCodes:  statusCodeMap,
		ErrorTypes:   errorTypeMap,
	}

	pretty, _ := json.MarshalIndent(result, "", "  ")
	log.Println(string(pretty))
	return result
}

func percentile(sorted []int64, p float64) float64 {
	if len(sorted) == 0 {
		return 0
	}

	index := int(float64(len(sorted)-1) * p)
	return float64(sorted[index]) / 1e6
}
