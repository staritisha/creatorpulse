"use client";
import { useState } from "react";
import Navbar from "../components/Navbar";
import Hero from "../components/Hero";
import LandscapeBg from "../components/LandscapeBg";

export default function Home() {
  const [night] = useState(false);
  return (
    <main style={{ position:"relative", minHeight:"100vh", overflow:"hidden" }}>
      <LandscapeBg night={night} />
      <div style={{ position:"relative", zIndex:10 }}>
        <Navbar />
        <Hero />
      </div>
    </main>
  );
}
