import { useEffect, useMemo, useState } from "react";
import "./App.css";
import AppShell from "./components/AppShell";
import LeaderboardTable from "./components/LeaderboardTable";
import OverviewPanel from "./components/OverviewPanel";
import ResultsPanel from "./components/ResultsPanel";
import SubmitPanel from "./components/SubmitPanel";
import { API_BASE, WS_URL } from "./lib/config";

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
  const [connectionState, setConnectionState] = useState("offline");
  const [currentSubmission, setCurrentSubmission] = useState(null);
  const [error, setError] = useState("");
  const [file, setFile] = useState(null);
  const [form, setForm] = useState({
    contestantName: "",
    language: "Python",
  });
  const [leaderboard, setLeaderboard] = useState([]);
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

    const formData = new FormData();
    formData.append("file", file);
    formData.append("contestant_name", form.contestantName || "Anonymous");
    formData.append("language", form.language);

    try {
      const response = await fetch(`${API_BASE}/submit`, {
        method: "POST",
        body: formData,
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Upload failed");
      }

      const submission = {
        submission_id: data.submission_id,
        contestant_name: data.contestant_name || form.contestantName || "Anonymous",
        language: data.language || form.language,
      };

      setCurrentSubmission(submission);
      setStatus({ ...submission, status: data.status });
      saveRecentSubmission(submission);
      setActiveView("results");
      setFile(null);
    } catch (uploadError) {
      setError(uploadError.message);
    } finally {
      setUploading(false);
    }
  }

  function selectSubmission(submission) {
    setCurrentSubmission(submission);
    setStatus({ ...submission, status: "waiting" });
    setActiveView("results");
  }

  async function fetchLeaderboard() {
    const response = await fetch(`${API_BASE}/leaderboard`);
    if (!response.ok) return;
    const data = await response.json();
    setLeaderboard(data);
  }

  useEffect(() => {
    if (!currentSubmissionId) return undefined;

    let cancelled = false;

    async function fetchStatus() {
      try {
        const response = await fetch(`${API_BASE}/status/${currentSubmissionId}`);
        if (!response.ok) return;
        const data = await response.json();
        if (!cancelled) {
          setStatus(data);
          if (["completed", "failed", "failed_handoff", "stopped"].includes(data.status)) {
            fetchLeaderboard();
          }
        }
      } catch {
        if (!cancelled) {
          setStatus((current) => ({
            ...current,
            status: current?.status || "waiting",
          }));
        }
      }
    }

    fetchStatus();
    const timer = window.setInterval(fetchStatus, 1500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [currentSubmissionId]);

  useEffect(() => {
    fetchLeaderboard();
  }, []);

  useEffect(() => {
    let ws;
    let reconnectTimer;

    function connect() {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => setConnectionState("online");
      ws.onmessage = (event) => setLeaderboard(JSON.parse(event.data));
      ws.onclose = () => {
        setConnectionState("offline");
        reconnectTimer = window.setTimeout(connect, 2500);
      };
      ws.onerror = () => {
        setConnectionState("offline");
        ws.close();
      };
    }

    connect();

    return () => {
      window.clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, []);

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
