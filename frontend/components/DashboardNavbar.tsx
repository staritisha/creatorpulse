export default function DashboardNavbar() {
    return (
      <nav className="flex justify-between items-center py-6 border-b border-gray-200 mb-10">
        <h1 className="text-2xl font-bold">
          CreatorPulse
        </h1>
  
        <div className="flex gap-4">
          <button className="px-4 py-2 border rounded-lg">
            Analytics
          </button>
  
          <button className="px-4 py-2 bg-black text-white rounded-lg">
            Dashboard
          </button>
        </div>
      </nav>
    );
  }