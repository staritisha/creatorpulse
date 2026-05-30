import Link from "next/link";

export default function Hero() {
  return (
    <section
      className="
      text-center
      py-32
      px-6
      "
    >
      <p className="text-blue-700 font-semibold">
        Creator Intelligence Platform
      </p>

      <h1
        className="
        text-8xl
        font-bold
        mt-6
        max-w-5xl
        mx-auto
        "
      >
        Understand What Content Resonates
      </h1>

      <p
        className="
        text-xl
        text-gray-800
        mt-8
        max-w-2xl
        mx-auto
        font-medium
        "
      >
        AI-powered creator insights across
        YouTube, Discord and Community Data.
      </p>

      <Link href="/dashboard">

        <button
          className="
          mt-10
          bg-white
          text-black
          px-8
          py-4
          rounded-full
          font-semibold
          "
        >
          Launch Dashboard
        </button>

      </Link>

    </section>
  );
}