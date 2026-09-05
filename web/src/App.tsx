/**
 * Landing view for the Wren client. The call interface — microphone capture,
 * call controls and the live transcript — is not wired up yet.
 */
export function App() {
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "3rem", lineHeight: 1.5 }}>
      <h1 style={{ margin: 0 }}>Wren</h1>
      <p style={{ color: "#555" }}>Home security troubleshooting voice agent.</p>
      <p style={{ color: "#888", fontSize: "0.9rem" }}>Services are up.</p>
    </main>
  );
}
