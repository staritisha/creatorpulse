"use client";
type Props = { night: boolean };

export default function LandscapeBg({ night }: Props) {
  return (
    <div className={`landscape-bg ${night ? "night" : "day"}`} aria-hidden>
      {/* Sun / Moon */}
      {!night ? (
        <div style={{
          position:"absolute", top:60, left:"50%", transform:"translateX(-50%) translateX(120px)",
          width:64, height:64, borderRadius:"50%",
          background:"radial-gradient(circle, #FFF5B0 20%, #FFD700 55%, #FF9500 100%)",
          boxShadow:"0 0 40px 20px rgba(255,200,50,0.35)",
          transition:"opacity 0.8s"
        }} />
      ) : (
        <div style={{
          position:"absolute", top:48, right:80,
          width:44, height:44, borderRadius:"50%",
          background:"radial-gradient(circle at 35% 35%, #FFFDE8, #E8E4C0)",
          boxShadow:"0 0 20px 8px rgba(230,220,180,0.15)",
        }} />
      )}

      {/* Stars (night only) */}
      {night && Array.from({length:50}).map((_,i) => (
        <div key={i} style={{
          position:"absolute",
          top:`${Math.sin(i*137.5)*40+20}%`,
          left:`${(i*137.5)%100}%`,
          width: i%3===0 ? 2:1, height: i%3===0 ? 2:1,
          borderRadius:"50%",
          background:"#fff",
          opacity: 0.4 + (i%5)*0.12,
        }} />
      ))}

      {/* Clouds (day only) */}
      {!night && (<>
        <div style={{
          position:"absolute", top:90, left:"15%",
          width:140, height:38, borderRadius:40,
          background:"rgba(255,255,255,0.82)",
          filter:"blur(2px)",
          transition:"opacity 0.8s",
        }} />
        <div style={{
          position:"absolute", top:70, left:"calc(15% + 60px)",
          width:80, height:28, borderRadius:40,
          background:"rgba(255,255,255,0.82)",
          filter:"blur(2px)",
        }} />
        <div style={{
          position:"absolute", top:110, right:"18%",
          width:110, height:32, borderRadius:40,
          background:"rgba(255,255,255,0.78)",
          filter:"blur(2px)",
        }} />
      </>)}

      {/* SVG scene */}
      <svg
        viewBox="0 0 1440 320"
        preserveAspectRatio="none"
        style={{ position:"absolute", bottom:0, left:0, width:"100%", height:"320px" }}
      >
        {/* Back hills */}
        <path d="M0,200 C180,140 360,180 540,160 C720,140 900,170 1080,155 C1260,140 1360,165 1440,155 L1440,320 L0,320 Z"
          fill={night ? "#1a1f4a" : "#7ec8a0"} opacity={0.7} />
        {/* Front hills */}
        <path d="M0,240 C120,200 280,220 440,205 C600,190 760,215 920,200 C1080,185 1260,210 1440,200 L1440,320 L0,320 Z"
          fill={night ? "#121638" : "#5bab78"} />
        {/* Lake strip */}
        <ellipse cx={720} cy={242} rx={260} ry={14}
          fill={night ? "rgba(70,100,200,0.18)" : "rgba(135,200,230,0.45)"} />
        {/* Hut – right side */}
        <rect x={1100} y={204} width={58} height={40} fill={night ? "#2a2055" : "#c8966e"} />
        <polygon points="1090,204 1168,204 1129,174" fill={night ? "#1e194a" : "#9b6b44"} />
        <rect x={1122} y={220} width={14} height={24} fill={night ? "#180f3a" : "#7a5233"} />
        {/* Left pine */}
        <polygon points="160,218 178,260 142,260" fill={night ? "#182040" : "#2d7a4f"} />
        <polygon points="163,200 178,230 148,230" fill={night ? "#1e2a52" : "#3a9060"} />
        {/* Right pine */}
        <polygon points="1300,215 1318,260 1282,260" fill={night ? "#182040" : "#2d7a4f"} />
        <polygon points="1303,196 1318,228 1288,228" fill={night ? "#1e2a52" : "#3a9060"} />
        {/* Foreground grass */}
        <rect x={0} y={290} width={1440} height={30} fill={night ? "#0e1235" : "#4a9e67"} />
      </svg>
    </div>
  );
}
