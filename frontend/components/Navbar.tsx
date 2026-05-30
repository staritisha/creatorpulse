import Link from "next/link";

export default function Navbar() {
  return (
    <nav className="cp-navbar">
      {/* Logo */}
      <div className="cp-logo-pill">
        {/* Waveform SVG */}
        <svg width="20" height="16" viewBox="0 0 20 16" fill="none">
          <rect x="0"  y="6"  width="3" height="4"  rx="1.5" fill="#6366f1"/>
          <rect x="4"  y="3"  width="3" height="10" rx="1.5" fill="#818cf8"/>
          <rect x="8"  y="0"  width="3" height="16" rx="1.5" fill="#a5b4fc"/>
          <rect x="12" y="3"  width="3" height="10" rx="1.5" fill="#818cf8"/>
          <rect x="16" y="6"  width="3" height="4"  rx="1.5" fill="#6366f1"/>
        </svg>
        <span className="cp-logo-text">CreatorPulse</span>
      </div>

      {/* Links */}
      <div style={{display:"flex", alignItems:"center", gap:28}}>
        <Link href="/" className="nav-link">Home</Link>
        <Link href="/dashboard" className="nav-link">Dashboard</Link>
        <Link href="/dashboard">
          <button className="btn-pill-dark">Get Started</button>
        </Link>
      </div>
    </nav>
  );
}
