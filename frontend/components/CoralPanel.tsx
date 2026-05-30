"use client";
import { useState } from "react";

const QUERIES = {
  resonance: {
    label: "📺 Top video",
    exec: "48ms",
    rows: "4 rows",
    chips: "c1",
    sql: `<span class="cm">-- resonance.sql · CreatorPulse</span>
<span class="kw">WITH</span> disc_agg <span class="kw">AS</span> (
  <span class="kw">SELECT</span> d.video_ref, <span class="fn">COUNT</span>(*) msg_count,
         <span class="fn">SUM</span>(d.total_reactions) reactions
  <span class="kw">FROM</span>   <span class="src">discord.messages</span> d
  <span class="kw">GROUP BY</span> d.video_ref
)
<span class="kw">SELECT</span> y.title, y.views, y.watch_pct,
       <span class="fn">COALESCE</span>(da.msg_count,0) discord_msgs,
       <span class="fn">SUM</span>(s.cta_clicks) cta_clicks,
       y.resonance_score
<span class="kw">FROM</span>   <span class="src">youtube.videos</span> y
<span class="kw">LEFT JOIN</span> disc_agg da         <span class="kw">ON</span> da.video_ref = y.video_id
<span class="kw">LEFT JOIN</span> <span class="src">gsheets.engagement_log</span> s <span class="kw">ON</span> s.video_id = y.video_id
<span class="kw">ORDER BY</span> y.resonance_score <span class="kw">DESC LIMIT</span> 20`,
  },
  trends: {
    label: "🔥 Trending topics",
    exec: "61ms",
    rows: "3 rows",
    chips: "c2",
    sql: `<span class="cm">-- trends.sql · CreatorPulse</span>
<span class="kw">SELECT</span> y.topic,
       <span class="fn">COUNT</span>(y.video_id)       videos_published,
       <span class="fn">AVG</span>(y.watch_pct)        avg_watch_pct,
       <span class="fn">COUNT</span>(d.message_id)     discord_msgs,
       <span class="fn">AVG</span>(y.resonance_score)  avg_resonance
<span class="kw">FROM</span>   <span class="src">youtube.videos</span> y
<span class="kw">LEFT JOIN</span> <span class="src">discord.messages</span>      d <span class="kw">ON</span> d.video_ref = y.video_id
<span class="kw">LEFT JOIN</span> <span class="src">gsheets.engagement_log</span> s <span class="kw">ON</span> s.video_id  = y.video_id
<span class="kw">GROUP BY</span> y.topic
<span class="kw">ORDER BY</span> avg_resonance <span class="kw">DESC</span>`,
  },
  discord: {
    label: "💬 Discord pulse",
    exec: "39ms",
    rows: "5 rows",
    chips: "c3",
    sql: `<span class="cm">-- discord_pulse.sql · CreatorPulse</span>
<span class="kw">SELECT</span> d.channel,
       <span class="fn">COUNT</span>(d.message_id)     total_msgs,
       <span class="fn">SUM</span>(d.total_reactions)  reactions,
       y.title                 video_title
<span class="kw">FROM</span>   <span class="src">discord.messages</span> d
<span class="kw">LEFT JOIN</span> <span class="src">youtube.videos</span> y <span class="kw">ON</span> y.video_id = d.video_ref
<span class="kw">WHERE</span>  d.timestamp &gt;= <span class="fn">NOW</span>() - <span class="kw">INTERVAL</span> '7 days'
<span class="kw">GROUP BY</span> d.channel, y.title
<span class="kw">ORDER BY</span> total_msgs <span class="kw">DESC</span>`,
  },
  next: {
    label: "🚀 What to make next",
    exec: "55ms",
    rows: "5 rows",
    chips: "c4",
    sql: `<span class="cm">-- next_best_content.sql · CreatorPulse</span>
<span class="kw">SELECT</span> y.topic,
       <span class="fn">MAX</span>(y.resonance_score)  peak_resonance,
       <span class="fn">COUNT</span>(d.message_id)     community_buzz,
       <span class="fn">AVG</span>(y.watch_pct)        avg_retention
<span class="kw">FROM</span>   <span class="src">youtube.videos</span> y
<span class="kw">LEFT JOIN</span> <span class="src">discord.messages</span>      d <span class="kw">ON</span> d.video_ref = y.video_id
<span class="kw">LEFT JOIN</span> <span class="src">gsheets.engagement_log</span> s <span class="kw">ON</span> s.video_id  = y.video_id
<span class="kw">GROUP BY</span> y.topic
<span class="kw">ORDER BY</span> peak_resonance <span class="kw">DESC LIMIT</span> 5`,
  },
  under: {
    label: "⚠️ Fix underperformers",
    exec: "43ms",
    rows: "2 rows",
    chips: "c5",
    sql: `<span class="cm">-- underperformers.sql · CreatorPulse</span>
<span class="kw">SELECT</span> y.title, y.views, y.watch_pct,
       <span class="fn">COALESCE</span>(<span class="fn">COUNT</span>(d.message_id),0) discord_msgs,
       <span class="kw">CASE</span>
         <span class="kw">WHEN</span> y.watch_pct &lt; 35 <span class="kw">THEN</span> 'low_retention'
         <span class="kw">WHEN</span> <span class="fn">COUNT</span>(d.message_id) &lt; 5 <span class="kw">THEN</span> 'community_silence'
         <span class="kw">ELSE</span> 'weak_engagement'
       <span class="kw">END</span> diagnosis
<span class="kw">FROM</span>   <span class="src">youtube.videos</span> y
<span class="kw">LEFT JOIN</span> <span class="src">discord.messages</span> d <span class="kw">ON</span> d.video_ref = y.video_id
<span class="kw">WHERE</span>  y.resonance_score &lt; 55
<span class="kw">GROUP BY</span> y.video_id
<span class="kw">ORDER BY</span> y.resonance_score <span class="kw">ASC LIMIT</span> 10`,
  },
};

type QueryKey = keyof typeof QUERIES;

interface Props {
  night?: boolean;
  showInputPanel?: boolean;
  onChipSelect?: (label: string) => void;
}

export default function CoralPanel({ night = false, showInputPanel = true, onChipSelect }: Props) {
  const [open, setOpen] = useState(true);
  const [active, setActive] = useState<QueryKey>("resonance");
  const [inputVal, setInputVal] = useState("");

  const q = QUERIES[active];

  const handleChip = (key: QueryKey) => {
    setActive(key);
    setInputVal(QUERIES[key].label);
    if (onChipSelect) onChipSelect(QUERIES[key].label);
  };

  const copySQL = () => {
    const el = document.querySelector(".sql-block") as HTMLElement;
    if (el) navigator.clipboard.writeText(el.innerText);
  };

  return (
    <div className={`coral-panel glass${night ? " glass-night" : ""}`}>
      {/* ─── Sub-panel 1: SQL Reveal ─── */}
      <div>
        {/* Header */}
        <div className="coral-header" onClick={() => setOpen(o => !o)}>
          {/* Coral organism icon */}
          <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
            <circle cx="11" cy="11" r="3" stroke="#FF9970" strokeWidth="1.5"/>
            <line x1="11" y1="8"  x2="11" y2="3"  stroke="#70D4FF" strokeWidth="1.5" strokeLinecap="round"/>
            <circle cx="11" cy="2.5" r="1.5" fill="#70D4FF"/>
            <line x1="9"  y1="12.7" x2="5"  y2="16.5" stroke="#6BCB77" strokeWidth="1.5" strokeLinecap="round"/>
            <circle cx="4.5" cy="17" r="1.5" fill="#6BCB77"/>
            <line x1="13" y1="12.7" x2="17" y2="16.5" stroke="#FFD93D" strokeWidth="1.5" strokeLinecap="round"/>
            <circle cx="17.5" cy="17" r="1.5" fill="#FFD93D"/>
          </svg>
          <span className="coral-label">Coral SQL — live query</span>
          <span className="badge-amber">3-source JOIN</span>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"
            style={{ transition:"transform 0.3s", transform: open ? "rotate(180deg)" : "rotate(0deg)" }}>
            <path d="M3 5l4 4 4-4" stroke="rgba(255,255,255,0.55)" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </div>

        {/* Body */}
        <div className={`coral-body${open ? " open" : ""}`}>
          <div style={{ padding:"12px 14px", display:"flex", flexDirection:"column", gap:10 }}>
            {/* Source pills row */}
            <div style={{ display:"flex", alignItems:"center", gap:6, flexWrap:"wrap" }}>
              <span className="source-pill sp-yt"><span className="src-dot" style={{background:"#ff4444"}}/>youtube.videos</span>
              <span className="source-pill sp-dc"><span className="src-dot" style={{background:"#5865F2"}}/>discord.messages</span>
              <span className="source-pill sp-gs"><span className="src-dot" style={{background:"#34A853"}}/>gsheets.engagement_log</span>
              <span style={{ marginLeft:"auto", fontFamily:"var(--font-mono)", fontSize:10, color:"rgba(255,255,255,0.35)" }}>
                executed in {q.exec}
              </span>
            </div>

            {/* SQL block */}
            <div className="sql-block" dangerouslySetInnerHTML={{ __html: q.sql }} />

            {/* Result row */}
            <div style={{
              display:"flex", alignItems:"center", justifyContent:"space-between",
              paddingTop:8, borderTop:"1px solid rgba(255,255,255,0.12)",
            }}>
              <span style={{ fontFamily:"var(--font-mono)", fontSize:10.5, color:"rgba(255,255,255,0.45)" }}>
                returned <span style={{ color:"#6BCB77", fontWeight:500 }}>{q.rows}</span>
              </span>
              <button onClick={copySQL} style={{
                background:"rgba(255,255,255,0.08)", border:"1px solid rgba(255,255,255,0.15)",
                borderRadius:6, padding:"3px 9px", fontFamily:"var(--font-mono)", fontSize:10,
                color:"rgba(255,255,255,0.5)", cursor:"pointer",
              }}>copy SQL</button>
            </div>
          </div>
        </div>
      </div>

      {/* Divider */}
      <div style={{ height:1, background:"rgba(255,255,255,0.1)" }} />

      {/* ─── Sub-panel 2: Query Input ─── */}
      {showInputPanel && (
        <div style={{ padding:"14px 14px 12px" }}>
          {/* Logo row */}
          <div style={{ display:"flex", alignItems:"center", gap:8, marginBottom:12 }}>
            <svg width="16" height="13" viewBox="0 0 20 16" fill="none">
              <rect x="0"  y="6"  width="3" height="4"  rx="1.5" fill="#6366f1"/>
              <rect x="4"  y="3"  width="3" height="10" rx="1.5" fill="#818cf8"/>
              <rect x="8"  y="0"  width="3" height="16" rx="1.5" fill="#a5b4fc"/>
              <rect x="12" y="3"  width="3" height="10" rx="1.5" fill="#818cf8"/>
              <rect x="16" y="6"  width="3" height="4"  rx="1.5" fill="#6366f1"/>
            </svg>
            <span style={{ fontFamily:"var(--font-display)", fontWeight:700, fontSize:12.5, color:"rgba(255,255,255,0.85)" }}>
              CreatorPulse
            </span>
          </div>

          {/* Input */}
          <div style={{ position:"relative", marginBottom:10 }}>
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none"
              style={{ position:"absolute", left:11, top:"50%", transform:"translateY(-50%)" }}>
              <circle cx="6.5" cy="6.5" r="4.5" stroke="rgba(255,255,255,0.4)" strokeWidth="1.5"/>
              <path d="M10 10l3.5 3.5" stroke="rgba(255,255,255,0.4)" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
            <input
              className="cp-input"
              value={inputVal}
              onChange={e => setInputVal(e.target.value)}
              placeholder="Ask Coral anything…"
              style={{ paddingLeft:28 }}
            />
          </div>

          {/* Query chips */}
          <div style={{ display:"flex", flexWrap:"wrap", gap:6, marginBottom:12 }}>
            {(Object.entries(QUERIES) as [QueryKey, typeof QUERIES[QueryKey]][]).map(([key, q]) => (
              <button
                key={key}
                className={`chip ${q.chips}${active === key ? " active" : ""}`}
                onClick={() => handleChip(key)}
              >
                {q.label}
              </button>
            ))}
          </div>

          {/* Status bar */}
          <div className="status-bar">
            <span className="src-dot-live" />
            Connected · YouTube · Discord · Google Sheets
            <span style={{ marginLeft:"auto", display:"flex", gap:4 }}>
              <span className="mini-badge mini-yt">YT</span>
              <span className="mini-badge mini-dc">DC</span>
              <span className="mini-badge mini-gs">GS</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
