interface Props {
  night?: boolean;
}

export default function SourceBadges({ night = false }: Props) {
  const sources = [
    { label: "youtube.videos",        count: "30 rows", dot: "#ff4444", pill: "sp-yt" },
    { label: "discord.messages",      count: "50 msgs",  dot: "#5865F2", pill: "sp-dc" },
    { label: "gsheets.engagement_log",count: "25 rows", dot: "#34A853", pill: "sp-gs" },
  ];

  return (
    <div style={{ display:"flex", flexDirection:"column", gap:10 }}>
      {sources.map((s) => (
        <div key={s.label} style={{
          display:"flex", alignItems:"center", justifyContent:"space-between",
          padding:"8px 10px",
          background:"rgba(0,0,0,0.12)", borderRadius:10,
          border:"1px solid rgba(255,255,255,0.08)",
        }}>
          <div style={{ display:"flex", alignItems:"center", gap:8 }}>
            <span style={{ width:8, height:8, borderRadius:"50%", background:s.dot, display:"inline-block" }} />
            <span style={{ fontFamily:"var(--font-mono)", fontSize:10.5, color:"rgba(255,255,255,0.7)" }}>
              {s.label}
            </span>
          </div>
          <div style={{ display:"flex", alignItems:"center", gap:6 }}>
            <span style={{ fontFamily:"var(--font-mono)", fontSize:10, color:"rgba(255,255,255,0.4)" }}>{s.count}</span>
            <span className="src-dot-live" style={{ width:6, height:6 }} />
          </div>
        </div>
      ))}
    </div>
  );
}
