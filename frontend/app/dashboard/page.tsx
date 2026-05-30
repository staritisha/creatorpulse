"use client";

import { useState } from "react";
import ResonanceCard from "../../components/ResonanceCard";
import SourceBadges from "../../components/SourceBadges";

export default function DashboardPage() {
  const [prompt, setPrompt] = useState("");

  return (
    <main className="min-h-screen bg-gradient-to-b from-white to-slate-50 text-black">
      
      {/* Navbar */}
      <nav className="border-b border-gray-200 bg-white/80 backdrop-blur sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-8 py-5 flex justify-between items-center">
          <h1 className="text-2xl font-bold">CreatorPulse</h1>

          <div className="flex gap-4">
            <button className="px-4 py-2 rounded-lg text-gray-600 hover:bg-gray-100">
              Analytics
            </button>

            <button className="px-4 py-2 rounded-lg bg-black text-white">
              Dashboard
            </button>
          </div>
        </div>
      </nav>

      <div className="max-w-7xl mx-auto px-8 py-12">

        {/* Hero */}
        <div className="mb-12">
          <p className="text-blue-600 font-semibold text-lg">
            AI Creator Intelligence
          </p>

          <h2 className="text-6xl font-extrabold mt-3 leading-tight">
            Understand What
            <br />
            Resonates With Your Audience
          </h2>

          <p className="text-xl text-gray-700 mt-6 max-w-3xl">
            Analyze creator performance, discover winning content,
            and get AI-powered recommendations backed by community data.
          </p>
        </div>

        {/* Main Layout */}
        <div className="grid lg:grid-cols-3 gap-8">

          {/* Left Side */}
          <div className="lg:col-span-2">

            {/* Quick Actions */}
            <div className="bg-white border border-gray-200 rounded-3xl p-8 shadow-sm">

              <h3 className="text-2xl font-bold mb-6">
                Quick Actions
              </h3>

              <div className="flex flex-wrap gap-4">

                <button
                  onClick={() =>
                    setPrompt("What should I make next?")
                  }
                  className="bg-gray-50 border border-gray-200 px-5 py-3 rounded-xl hover:bg-gray-100 transition"
                >
                  What should I make next?
                </button>

                <button
                  onClick={() =>
                    setPrompt("Why did my content underperform?")
                  }
                  className="bg-gray-50 border border-gray-200 px-5 py-3 rounded-xl hover:bg-gray-100 transition"
                >
                  Why did my content underperform?
                </button>

                <button
                  onClick={() =>
                    setPrompt("What builds loyal community?")
                  }
                  className="bg-gray-50 border border-gray-200 px-5 py-3 rounded-xl hover:bg-gray-100 transition"
                >
                  What builds loyal community?
                </button>

              </div>

              {/* Input Area */}
              <div className="mt-8 flex gap-4">

                <input
                  type="text"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="Ask anything about your audience..."
                  className="
                    flex-1
                    border
                    border-gray-300
                    rounded-xl
                    px-4
                    py-4
                    bg-white
                    focus:outline-none
                    focus:ring-2
                    focus:ring-blue-500
                  "
                />

                <button
                  className="
                    bg-black
                    text-white
                    px-8
                    rounded-xl
                    hover:opacity-90
                  "
                >
                  Send
                </button>

              </div>
            </div>

            {/* Placeholder Chat Area */}
            <div className="mt-8 bg-white border border-gray-200 rounded-3xl p-8 shadow-sm min-h-[300px]">

              <h3 className="text-xl font-semibold mb-4">
                AI Insights
              </h3>

              <p className="text-gray-600">
                Ask a question above to receive AI-powered creator insights.
              </p>

            </div>

          </div>

          {/* Right Side */}
          <div className="space-y-6">

            <ResonanceCard
              score={92}
              title="SQL Masterclass"
              insight="Generated the highest number of returning viewers."
            />

            <div className="bg-white border border-gray-200 rounded-3xl p-6 shadow-sm">

              <h3 className="font-semibold mb-4">
                Data Sources
              </h3>

              <SourceBadges />

            </div>

            <div className="bg-white border border-gray-200 rounded-3xl p-6 shadow-sm">

              <h3 className="font-semibold mb-4">
                Audience Snapshot
              </h3>

              <div className="space-y-4">

                <div className="flex justify-between">
                  <span>Retention</span>
                  <span className="font-semibold">84%</span>
                </div>

                <div className="flex justify-between">
                  <span>Engagement</span>
                  <span className="font-semibold">71%</span>
                </div>

                <div className="flex justify-between">
                  <span>Community Growth</span>
                  <span className="font-semibold text-green-600">
                    +18%
                  </span>
                </div>

              </div>

            </div>

          </div>

        </div>

      </div>

    </main>
  );
}