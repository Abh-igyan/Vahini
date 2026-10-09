import { useEffect, useMemo, useState } from "react";
import "./App.css";
import AppShell from "./components/AppShell";
import LeaderboardTable from "./components/LeaderboardTable";
import OverviewPanel from "./components/OverviewPanel";
import ResultsPanel from "./components/ResultsPanel";
import SubmitPanel from "./components/SubmitPanel";

const STORAGE_KEY = "iicpc_recent_submissions";

function readRecentSubmissions() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
  } catch {
    return [];
  }
}

function App() {
  const [activeView, setActiveView] = useState("overview");
  const [connectionState] = useState("online"); // Hardcoded to online for static demo
  const [currentSubmission, setCurrentSubmission] = useState(null);
  const [error, setError] = useState("");
  const [file, setFile] = useState(null);
  const [form, setForm] = useState({
    contestantName: "",
    language: "Python",
  });
  const [leaderboard, setLeaderboard] = useState([
    // Initial mock leaderboard data
    {
      submission_id: "demo1234",
      contestant_name: "Demo User",
      language: "Python",
      score: 8.5,
      correctness_score: 40,
      tps: 120.5,
      p50_latency_ms: 12,
      p90_latency_ms: 18,
      p99_latency_ms: 25,
      failures: 2,
    }
  ]);
  const [recentSubmissions, setRecentSubmissions] = useState(readRecentSubmissions);
  const [status, setStatus] = useState(null);
  const [uploading, setUploading] = useState(false);

  const currentSubmissionId = currentSubmission?.submission_id;

  const sortedLeaderboard = useMemo(
    () => [...leaderboard].sort((a, b) => Number(b.score || 0) - Number(a.score || 0)),
    [leaderboard],
  );

  function saveRecentSubmission(submission) {
    const next = [
      submission,
      ...recentSubmissions.filter((item) => item.submission_id !== submission.submission_id),
    ].slice(0, 8);

    setRecentSubmissions(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }

  function handleFormChange(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
  }

  function handleFileChange(event) {
    setFile(event.target.files?.[0] || null);
    setError("");
  }

  async function uploadSubmission(event) {
    event.preventDefault();
    if (!file || uploading) return;

    setUploading(true);
    setError("");

    // Mock upload network request
    setTimeout(() => {
      const newId = Math.random().toString(36).substring(2, 10);
      const submission = {
        submission_id: newId,
        contestant_name: form.contestantName || "Anonymous",
        language: form.language,
      };

      setCurrentSubmission(submission);
      setStatus({ ...submission, status: "waiting" });
      saveRecentSubmission(submission);
      setActiveView("results");
      setFile(null);
      setUploading(false);
    }, 1500);
  }

  function selectSubmission(submission) {
    setCurrentSubmission(submission);
    // If it's a known demo submission or from cache, mock it to completed
    setStatus({ ...submission, status: "completed", score: 7.5, correctness_score: 30, tps: 0.0, failures: 798, correctness_checks: { empty_book: true, market_order_execution: true, cancellation: true } });
    setActiveView("results");
  }

  // Mock progression of status for new submissions
  useEffect(() => {
    if (!currentSubmissionId) return undefined;

    let timer;
    if (status?.status === "waiting") {
      timer = setTimeout(() => {
        setStatus(current => ({ ...current, status: "processing" }));
      }, 2000);
    } else if (status?.status === "processing") {
      timer = setTimeout(() => {
        const mockResult = {
          status: "completed",
          score: (Math.random() * 5 + 5).toFixed(2), // Random score between 5 and 10
          correctness_score: 40,
          tps: (Math.random() * 200).toFixed(2),
          success: 1.0,
          p50_latency_ms: (Math.random() * 10 + 5).toFixed(2),
          p90_latency_ms: (Math.random() * 15 + 10).toFixed(2),
          p99_latency_ms: (Math.random() * 20 + 15).toFixed(2),
          failures: Math.floor(Math.random() * 10),
          correctness_checks: {
            empty_book: true,
            duplicate_orders: true,
            invalid_side: true,
            market_order_execution: true,
            price_time_priority: true,
            multiple_fills: true,
            partial_fills: false,
            cancellation: true,
            remaining_quantity: true,
          }
        };
        setStatus(current => ({ ...current, ...mockResult }));
        
        setLeaderboard(prev => {
          const entry = {
            submission_id: currentSubmissionId,
            contestant_name: status.contestant_name || "Anonymous",
            language: status.language,
            ...mockResult
          };
          return [...prev.filter(p => p.submission_id !== currentSubmissionId), entry];
        });

      }, 3500);
    }

    return () => clearTimeout(timer);
  }, [currentSubmissionId, status?.status, status?.contestant_name, status?.language]);

  return (
    <AppShell
      activeView={activeView}
      connectionState={connectionState}
      onViewChange={setActiveView}
      submissionCount={recentSubmissions.length}
    >
      {activeView === "overview" ? (
        <OverviewPanel onStart={() => setActiveView("submit")} />
      ) : null}

      {activeView === "submit" ? (
        <SubmitPanel
          error={error}
          file={file}
          form={form}
          onChange={handleFormChange}
          onFileChange={handleFileChange}
          onSubmit={uploadSubmission}
          uploading={uploading}
        />
      ) : null}

      {activeView === "results" ? (
        <ResultsPanel
          currentSubmission={currentSubmission}
          leaderboard={sortedLeaderboard}
          onSelectSubmission={selectSubmission}
          recentSubmissions={recentSubmissions}
          status={status}
        />
      ) : null}

      {activeView === "leaderboard" ? (
        <LeaderboardTable
          currentSubmissionId={currentSubmissionId}
          leaderboard={sortedLeaderboard}
        />
      ) : null}
    </AppShell>
  );
}

export default App;
