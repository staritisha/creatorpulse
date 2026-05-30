import Navbar from "../components/Navbar";
import Hero from "../components/Hero";

export default function Home() {
  return (
    <main className="relative min-h-screen bg-white text-black overflow-hidden">
      <div className="hero-gradient"></div>

      <div className="relative z-10">
        <Navbar />
        <Hero />
      </div>
    </main>
  );
}