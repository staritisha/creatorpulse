"use client";
import { useEffect, useState } from "react";

interface Props {
  score?: number;
  title?: string;
  insight?: string;
  night?: boolean;
}

const SPARKLINE = [40, 52, 48, 61, 58, 72, 92];
const CIRCUMFERENCE = 2 * Math.PI * 48; // r=48

export default function ResonanceCard({
  score = 92,
  title = "AI Agents — LangGraph",
  insight = "4.1× Discord spike · 66% retention · viral window open",
  night = false,
}: Props) {
  const [animated, setAnimated] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setAnimated(true), 100);
    return () => clearTimeout(t);
  }, []);

  const pct = score / 100;
  const offset = CIRCUMFERENCE * (1 - pct);
  const color = score >= 80 ? "#22c55e" : score >= 60 ? "#6366f1" : score >= 40 ? "#FFD93D" : "#ef4444";
  const tier = score >= 80 ? "High Resonance" : score >= 60 ? "Good" : "Needs Work";

  const maxBar = Math.max(...SPARKLINE);

  return (
    <div className={`glass${night ? " glass-night" : ""}`} style={{ padding:"18px 20px" }}>
      {/* Header */}
      <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:16 }}>
        <span style={{ fontFamily:"var(--font-body)", fontWeight:500, fontSize:12.5, color:"rgba(255,255,255,0.6)" }}>
          Resonance Score
        </span>
        <span style={{
          background: score >= 80 ? "rgba(34,197,94,0.18)" : "rgba(99,102,241,0.18)",
          color: color, border:`1px solid ${color}40`,
          borderRadius:100, padding:"2px 10px", fontSize:10.5,
          fontFamily:"var(--font-body)", fontWeight:500,
        }}>
          {tier}
        </span>
      </div>

      {/* Ring + score */}
      <div style={{ display:"flex", alignItems:"center", gap:18, marginBottom:14 }}>
        <svg width={96} height={96} viewBox="0 0 96 96">
          {/* Track */}
          <circle cx={48} cy={48} r={38} fill="none" stroke="rgba(255,255,255,0.12)" strokeWidth={7} />
          {/* Fill */}
          <circle cx={48} cy={48} r={38} fill="none"
            stroke={color} strokeWidth={7}
            strokeLinecap="round"
            strokeDasharray={`${2*Math.PI*38}`}
            strokeDashoffset={animated ? `${2*Math.PI*38*(1-pct)}` : `${2*Math.PI*38}`}
            transform="rotate(-90 48 48)"
            style={{ transition:"stroke-dashoffset 1.2s cubic-bezier(0.4,0,0.2,1)" }}
          />
          {/* Score text */}
          <text x={48} y={44} textAnchor="middle" dominantBaseline="middle"
            fontFamily="Syne, sans-serif" fontWeight={800} fontSize={24} fill="rgba(255,255,255,0.92)">
            {score}
          </text>
          <text x={48} y={60} textAnchor="middle"
            fontFamily="DM Sans, sans-serif" fontSize={10} fill="rgba(255,255,255,0.45)">
            /100
          </text>
        </svg>

        <div>
          <div style={{ fontFamily:"var(--font-display)", fontWeight:700, fontSize:14, color:"rgba(255,255,255,0.9)", marginBottom:6, lineHeight:1.3 }}>
            {title}
          </div>
          <div style={{ fontFamily:"var(--font-body)", fontSize:11.5, color:"rgba(255,255,255,0.5)", lineHeight:1.5 }}>
            {insight}
          </div>
        </div>
      </div>

      {/* Sparkline */}
      <div style={{ borderTop:"1px solid rgba(255,255,255,0.1)", paddingTop:12 }}>
        <div style={{ display:"flex", alignItems:"flex-end", gap:3, height:28, marginBottom:6 }}>
          {SPARKLINE.map((v, i) => (
            <div key={i} style={{
              flex:1, borderRadius:2,
              height:`${(v/maxBar)*100}%`,
              background: i === SPARKLINE.length-1 ? color : "rgba(255,255,255,0.18)",
              transition:"height 0.6s ease",
            }} />
          ))}
        </div>
        <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center" }}>
          <span style={{ fontFamily:"var(--font-mono)", fontSize:10, color:"rgba(255,255,255,0.35)" }}>7-day trend</span>
          <span style={{ fontFamily:"var(--font-body)", fontWeight:500, fontSize:11.5, color:"#22c55e" }}>↑ +6.8%</span>
        </div>
      </div>
    </div>
  );
}
