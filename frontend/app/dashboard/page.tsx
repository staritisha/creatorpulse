"use client";

import { useState, useRef, useEffect } from "react";
import LandscapeBg from "../../components/LandscapeBg";
import DashboardNavbar from "../../components/DashboardNavbar";
import ResonanceCard from "../../components/ResonanceCard";
import SourceBadges from "../../components/SourceBadges";
import CoralPanel from "../../components/CoralPanel";

/* ─── Types ─── */
interface Message { role:"user"|"ai"; text:string; loading?:boolean; }

/* ─── Mock responses ─── */
const MOCK_RESPONSES: Record<string, string> = {
  "🎯 What should I make next?":
    "Based on your Coral data: 🔥 **AI Agents** — 87/100 avg resonance (+8.5 pts trending). Discord shows 3.2× spike vs baseline. **Recommendation:** Publish a LangGraph follow-up within 72 hours. The viral window is still open.",
  "📉 Why did my content underperform?":
    "⚠️ **Career Q&A #12** — 180k views but only 22% retention and 3 Discord messages. **Diagnosis:** CTR-retention mismatch. **Fix:** Deliver the core answer in the first 45 seconds.",
  "💬 What builds loyal community?":
    "💬 **68 loyalty index** — up 23% week-over-week. Your Discord community is most active after AI Agents uploads. **Action:** Pin a discussion thread after each upload.",
};

const WELCOME = "👋 Welcome to CreatorPulse! I've analyzed your channel data across YouTube, Discord, and Google Sheets using Coral SQL. 🔥 **AI Agents** content has 4.1× community engagement — it's your biggest growth lever right now. Ask me anything about your audience or content strategy.";

/* ─── Leaderboard data ─── */
const LEADERBOARD = [
  { title:"Building an AI Agent from Scratch",  topic:"AI Agents", score:91, delta:"+12 pts", pos:true },
  { title:"LangGraph Deep Dive",                topic:"AI Agents", score:84, delta:"+5 pts",  pos:true },
  { title:"System Design for Senior Engs",      topic:"Backend",   score:76, delta:"+3.5 pts",pos:true },
  { title:"FastAPI + PostgreSQL Tutorial",       topic:"Backend",   score:70, delta:"+1.2 pts",pos:true },
  { title:"Career Q&A #12",                      topic:"Career",    score:31, delta:"-8 pts",  pos:false },
];

function topicColor(t: string) {
  if (t==="AI Agents") return { bg:"rgba(99,102,241,0.2)", color:"#a5b4fc" };
  if (t==="Backend")   return { bg:"rgba(34,197,94,0.18)", color:"#86efac" };
  return                      { bg:"rgba(255,211,61,0.18)", color:"#fde68a" };
}

function scoreColor(s: number) {
  if (s>=80) return "#22c55e";
  if (s>=60) return "#6366f1";
  if (s>=40) return "#FFD93D";
  return "#ef4444";
}

export default function DashboardPage() {
  const [night, setNight] = useState(false);
  const [messages, setMessages] = useState<Message[]>([
    { role:"ai", text: WELCOME },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const chatRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
    }
  }, [messages]);

  const sendMessage = async (text: string) => {
    if (!text.trim() || loading) return;
    const userMsg = text.trim();
    setInput("");
    setMessages(m => [...m, { role:"user", text:userMsg }]);
    setLoading(true);

    // Check mock first
    const mockKey = Object.keys(MOCK_RESPONSES).find(k => userMsg.includes(k) || k.includes(userMsg));
    if (mockKey) {
      await new Promise(r => setTimeout(r, 600));
      setMessages(m => [...m, { role:"ai", text: MOCK_RESPONSES[mockKey] }]);
      setLoading(false);
      return;
    }

    // Try real backend via SSE
    try {
      const res = await fetch("http://localhost:8000/api/v1/chat", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ message: userMsg, demo_mode:true }),
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok || !res.body) throw new Error();
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let aiText = "";
      setMessages(m => [...m, { role:"ai", text:"", loading:true }]);
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value);
        for (const line of chunk.split("\n")) {
          if (line.startsWith("data: ")) {
            const d = line.slice(6).trim();
            if (d==="[DONE]") break;
            try { aiText += JSON.parse(d).token ?? d; } catch { aiText += d; }
            setMessages(m => [...m.slice(0,-1), { role:"ai", text:aiText }]);
          }
        }
      }
      setMessages(m => [...m.slice(0,-1), { role:"ai", text:aiText || "Done." }]);
    } catch {
      setMessages(m => [
        ...m,
        { role:"ai", text: "Backend offline — running in demo mode. Try: \"What should I make next?\"" },
      ]);
    }
    setLoading(false);
  };

  const QUICK = [
    "🎯 What should I make next?",
    "📉 Why did my content underperform?",
    "💬 What builds loyal community?",
  ];

  return (
    <main style={{ position:"relative", minHeight:"100vh", overflow:"hidden" }}>
      <LandscapeBg night={night} />

      <div style={{ position:"relative", zIndex:10, minHeight:"100vh", display:"flex", flexDirection:"column" }}>
        <DashboardNavbar night={night} onToggle={() => setNight(n => !n)} />

        {/* Content area */}
        <div style={{
          flex:1, maxWidth:1280, width:"100%", margin:"0 auto",
          padding:"28px 24px 40px", display:"flex", flexDirection:"column", gap:20,
        }}>

          {/* Page header */}
          <div className="anim-fade-up" style={{ marginBottom:4 }}>
            <p style={{ fontFamily:"var(--font-body)", fontSize:11.5, fontWeight:500, letterSpacing:"0.08em", textTransform:"uppercase", color:"var(--indigo)", marginBottom:8 }}>
              AI Creator Intelligence
            </p>
            <h1 style={{
              fontFamily:"var(--font-display)", fontWeight:800,
              fontSize:"clamp(26px, 3.5vw, 42px)", lineHeight:1.1,
              letterSpacing:"-0.03em", color: night ? "rgba(255,255,255,0.95)" : "#0f1a2e",
              marginBottom:8,
            }}>
              Understand What&nbsp;
              <span className="gradient-text">Resonates With Your Audience</span>
            </h1>
          </div>

          {/* Two-column grid */}
          <div style={{ display:"grid", gridTemplateColumns:"2fr 1fr", gap:18, alignItems:"start" }}>

            {/* ─── LEFT COLUMN ─── */}
            <div style={{ display:"flex", flexDirection:"column", gap:16 }}>

              {/* Quick Actions */}
              <div className={`glass${night?" glass-night":""} anim-fade-up delay-1`} style={{ padding:"18px 20px" }}>
                <div style={{ display:"flex", gap:10, flexWrap:"wrap", marginBottom:16 }}>
                  {QUICK.map(q => (
                    <button key={q} className="quick-chip" onClick={() => sendMessage(q)}>{q}</button>
                  ))}
                </div>

                {/* Chat input */}
                <div style={{ position:"relative", display:"flex", alignItems:"center" }}>
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none"
                    style={{ position:"absolute", left:12, pointerEvents:"none" }}>
                    <circle cx="6.5" cy="6.5" r="4.5" stroke="rgba(255,255,255,0.4)" strokeWidth="1.5"/>
                    <path d="M10 10l3.5 3.5" stroke="rgba(255,255,255,0.4)" strokeWidth="1.5" strokeLinecap="round"/>
                  </svg>
                  <input
                    className="cp-input"
                    style={{ paddingLeft:32, paddingRight:48, flex:1 }}
                    placeholder="Ask anything about your audience or content…"
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={e => e.key==="Enter" && sendMessage(input)}
                  />
                  <button
                    onClick={() => sendMessage(input)}
                    style={{
                      position:"absolute", right:8,
                      width:30, height:30, borderRadius:9,
                      background:"#fff", border:"none", cursor:"pointer",
                      display:"flex", alignItems:"center", justifyContent:"center",
                    }}
                  >
                    <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
                      <path d="M1 13L13 7 1 1v5l8 1-8 1v5z" fill="#6366f1"/>
                    </svg>
                  </button>
                </div>
              </div>

              {/* AI Chat Panel */}
              <div className={`glass${night?" glass-night":""} anim-fade-up delay-2`}
                style={{ padding:"18px 20px", minHeight:260 }}>
                <div style={{ fontFamily:"var(--font-body)", fontWeight:500, fontSize:12.5, color: night ? "rgba(255,255,255,0.55)" : "rgba(0,0,0,0.45)", marginBottom:14 }}>
                  AI Insights
                </div>

                {/* Messages */}
                <div ref={chatRef} style={{ display:"flex", flexDirection:"column", gap:12, maxHeight:320, overflowY:"auto", paddingRight:4 }}>
                  {messages.map((m, i) => (
                    <div key={i} style={{ display:"flex", alignItems:"flex-start", gap:8,
                      justifyContent: m.role==="user" ? "flex-end" : "flex-start" }}>
                      {m.role==="ai" && <div className="ai-avatar">🤖</div>}
                      <div className={m.role==="user" ? "bubble-user" : "bubble-ai"}
                        style={{ fontSize:13 }}
                        dangerouslySetInnerHTML={{ __html: m.text.replace(/\*\*(.*?)\*\*/g,"<strong>$1</strong>") }}
                      />
                    </div>
                  ))}
                  {loading && (
                    <div style={{ display:"flex", alignItems:"flex-start", gap:8 }}>
                      <div className="ai-avatar">🤖</div>
                      <div className="bubble-ai" style={{ display:"flex", gap:5, alignItems:"center", padding:"12px 16px" }}>
                        {[0,1,2].map(i => (
                          <span key={i} className="bounce-dot" style={{ animationDelay:`${i*0.15}s` }} />
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Content Leaderboard */}
              <div className={`glass${night?" glass-night":""} anim-fade-up delay-3`} style={{ padding:"18px 20px" }}>
                <div style={{ fontFamily:"var(--font-body)", fontWeight:500, fontSize:12.5, color: night ? "rgba(255,255,255,0.55)" : "rgba(0,0,0,0.45)", marginBottom:14 }}>
                  Content Leaderboard
                </div>
                <div style={{ display:"flex", flexDirection:"column", gap:10 }}>
                  {LEADERBOARD.map((v, i) => {
                    const tc = topicColor(v.topic);
                    const sc = scoreColor(v.score);
                    return (
                      <div key={i} style={{ display:"flex", alignItems:"center", gap:12, padding:"8px 0",
                        borderBottom: i<LEADERBOARD.length-1 ? "1px solid rgba(255,255,255,0.07)" : "none" }}>
                        <span style={{ fontFamily:"var(--font-mono)", fontSize:10, color: night ? "rgba(255,255,255,0.3)" : "rgba(0,0,0,0.35)", width:14, textAlign:"right" }}>
                          {i+1}
                        </span>
                        <div style={{ flex:1, minWidth:0 }}>
                          <div style={{ fontFamily:"var(--font-body)", fontSize:12.5, color: night ? "rgba(255,255,255,0.85)" : "rgba(0,0,0,0.80)",
                            whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis", marginBottom:4 }}>
                            {v.title}
                          </div>
                          <div style={{ display:"flex", alignItems:"center", gap:8 }}>
                            <span className="topic-badge" style={{ background:tc.bg, color:tc.color }}>{v.topic}</span>
                            <div className="score-bar-track" style={{ flex:1 }}>
                              <div className="score-bar-fill" style={{ width:`${v.score}%`, background:sc }} />
                            </div>
                          </div>
                        </div>
                        <div style={{ display:"flex", alignItems:"center", gap:8, flexShrink:0 }}>
                          <span style={{ fontFamily:"var(--font-display)", fontWeight:700, fontSize:18, color:sc }}>{v.score}</span>
                          <span style={{ fontFamily:"var(--font-body)", fontSize:10.5,
                            color: v.pos ? "#22c55e" : "#ef4444", minWidth:50, textAlign:"right" }}>
                            {v.delta}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* ─── RIGHT COLUMN ─── */}
            <div style={{ display:"flex", flexDirection:"column", gap:16 }}>

              {/* Resonance Score */}
              <div className="anim-scale-in delay-1">
                <ResonanceCard night={night} />
              </div>

              {/* Coral Panel */}
              <div className="anim-scale-in delay-2">
                <CoralPanel night={night} showInputPanel={false} />
              </div>

              {/* Data Sources */}
              <div className={`glass${night?" glass-night":""} anim-scale-in delay-3`} style={{ padding:"16px 18px" }}>
                <div style={{ fontFamily:"var(--font-body)", fontWeight:500, fontSize:12.5, color: night ? "rgba(255,255,255,0.55)" : "rgba(0,0,0,0.45)", marginBottom:12 }}>
                  Data Sources
                </div>
                <SourceBadges night={night} />
              </div>

              {/* Audience Snapshot */}
              <div className={`glass${night?" glass-night":""} anim-scale-in delay-4`} style={{ padding:"16px 18px" }}>
                <div style={{ fontFamily:"var(--font-body)", fontWeight:500, fontSize:12.5, color: night ? "rgba(255,255,255,0.55)" : "rgba(0,0,0,0.45)", marginBottom:14 }}>
                  Audience Snapshot
                </div>
                {[
                  { label:"Retention",         val:84,  color:"#22c55e",   display:"84%" },
                  { label:"Engagement",         val:71,  color:"#6366f1",   display:"71%" },
                  { label:"Community Growth",   val:18,  color:"#22c55e",   display:"+18%" },
                ].map(m => (
                  <div key={m.label} className="audience-row" style={{ marginBottom:14 }}>
                    <div className="audience-row-header">
                      <span style={{ fontFamily:"var(--font-body)", fontSize:12.5, color: night ? "rgba(255,255,255,0.7)" : "rgba(0,0,0,0.65)" }}>{m.label}</span>
                      <span style={{ fontFamily:"var(--font-display)", fontWeight:700, fontSize:15, color: night ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.85)" }}>
                        {m.display}
                      </span>
                    </div>
                    <div className="score-bar-track" style={{ background: night ? "rgba(255,255,255,0.15)" : "rgba(0,0,0,0.10)" }}>
                      <div className="score-bar-fill" style={{ width:`${m.val}%`, background:m.color }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}