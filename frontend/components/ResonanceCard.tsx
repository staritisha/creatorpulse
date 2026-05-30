type Props = {
    score: number;
    title: string;
    insight: string;
  };
  
  export default function ResonanceCard({
    score,
    title,
    insight,
  }: Props) {
    let color = "bg-red-500";
  
    if (score >= 80) {
      color = "bg-green-500";
    } else if (score >= 50) {
      color = "bg-yellow-500";
    }
  
    return (
      <div className="bg-white rounded-3xl p-6 border border-gray-200 shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">
            Resonance Score
          </h3>
  
          <div
            className={`w-4 h-4 rounded-full ${color}`}
          />
        </div>
  
        <div className="text-5xl font-bold mb-4">
          {score}
        </div>
  
        <div className="text-xl font-semibold mb-2">
          {title}
        </div>
  
        <p className="text-gray-600">
          {insight}
        </p>
      </div>
    );
  }