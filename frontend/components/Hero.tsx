"use client";
import Link from "next/link";
import CoralPanel from "./CoralPanel";

export default function Hero() {
  return (
    <section style={{
      display:"flex", flexDirection:"column", alignItems:"center",
      textAlign:"center", paddingTop:120, paddingBottom:80,
      paddingLeft:24, paddingRight:24,
    }}>
      <div className="hackathon-badge anim-fade-up delay-1" style={{ marginBottom:32 }}>
        🏴‍☠️ Pirates of the Coral-bean · Personal Agent Track
      </div>

      <h1 className="hero-headline anim-fade-up delay-2" style={{ marginBottom:20 }}>
        Understand What<br/>
        <span className="gradient-text">Content Resonates</span>
      </h1>

      <p className="hero-subhead anim-fade-up delay-3" style={{ marginBottom:36, margin:"0 auto 36px" }}>
        AI-powered creator intelligence across YouTube, Discord and Google Sheets — joined in a single Coral SQL query.
      </p>

      <div className="anim-fade-up delay-4" style={{ display:"flex", gap:12, marginBottom:48, flexWrap:"wrap", justifyContent:"center" }}>
        <Link href="/dashboard">
          <button className="btn-primary">Launch Dashboard →</button>
        </Link>
        <button className="btn-outline">⭐ Star on GitHub</button>
      </div>

      <div className="anim-scale-in delay-5" style={{ width:"100%", maxWidth:620, marginBottom:32 }}>
        <CoralPanel showInputPanel={true} />
      </div>

      <div className="anim-fade-up delay-6" style={{ display:"flex", gap:8, flexWrap:"wrap", justifyContent:"center" }}>
        {["📊 Cross-source JOINs","🤖 AI Insights","⚡ Real-time","🔁 SSE Streaming"].map(f => (
          <span key={f} className="feature-pill">{f}</span>
        ))}
      </div>
    </section>
  );
}
