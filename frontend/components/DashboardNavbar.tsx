import Link from "next/link";

interface Props {
  night: boolean;
  onToggle: () => void;
}

export default function DashboardNavbar({ night, onToggle }: Props) {
  return (
    <nav style={{
      position:"sticky", top:0, zIndex:100,
      display:"flex", alignItems:"center", justifyContent:"space-between",
      padding:"0 32px", height:58,
      background: night ? "rgba(10,8,32,0.55)" : "rgba(255,255,255,0.18)",
      backdropFilter:"blur(18px) saturate(1.4)",
      WebkitBackdropFilter:"blur(18px) saturate(1.4)",
      borderBottom:`1px solid ${night ? "rgba(160,130,255,0.15)" : "rgba(255,255,255,0.3)"}`,
    }}>
      {/* Logo */}
      <div style={{ display:"flex", alignItems:"center", gap:9 }}>
        <svg width="16" height="13" viewBox="0 0 20 16" fill="none">
          <rect x="0"  y="6"  width="3" height="4"  rx="1.5" fill="#6366f1"/>
          <rect x="4"  y="3"  width="3" height="10" rx="1.5" fill="#818cf8"/>
          <rect x="8"  y="0"  width="3" height="16" rx="1.5" fill="#a5b4fc"/>
          <rect x="12" y="3"  width="3" height="10" rx="1.5" fill="#818cf8"/>
          <rect x="16" y="6"  width="3" height="4"  rx="1.5" fill="#6366f1"/>
        </svg>
        <span style={{ fontFamily:"var(--font-display)", fontWeight:700, fontSize:14.5, color:"rgba(255,255,255,0.9)" }}>
          CreatorPulse
        </span>
      </div>

      {/* Right */}
      <div style={{ display:"flex", alignItems:"center", gap:16 }}>
        <Link href="/" style={{ fontFamily:"var(--font-body)", fontSize:13, color:"rgba(255,255,255,0.6)", textDecoration:"none" }}>
          Landing
        </Link>

        {/* Day/Night toggle */}
        <button
          className={`dn-toggle ${night ? "night" : "day"}`}
          onClick={onToggle}
          aria-label="Toggle day/night"
        >
          <div className={`dn-knob ${night ? "night" : "day"}`}>
            {night ? "🌙" : "☀️"}
          </div>
        </button>
      </div>
    </nav>
  );
}
