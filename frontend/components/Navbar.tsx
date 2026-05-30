import Link from "next/link";

export default function Navbar() {
  return (
    <nav className="flex justify-between items-center px-10 py-6">

      <h1 className="text-xl font-bold">
        CreatorPulse
      </h1>

      <div className="flex items-center gap-8">

        <Link href="/">
          Home
        </Link>

        <Link href="/dashboard">
          Dashboard
        </Link>

        <button
          className="
          bg-white
          text-black
          px-5
          py-2
          rounded-full
          "
        >
          Get Started
        </button>

      </div>

    </nav>
  );
}