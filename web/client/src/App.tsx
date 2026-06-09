import React from 'react';
import { Header } from './components/Header';
import { EdgeCard } from './components/EdgeCard';
import { AlertFeed } from './components/AlertFeed';

function App() {
  // Mock data for the UI
  const mockProps = [
    { id: 1, player: "A'ja Wilson", team: "LVA", stat: "Points", line: 22.5, projection: 24.8, bookOdds: -110, trueOdds: 62.4, edge: 6.8, isOver: true },
    { id: 2, player: "Breanna Stewart", team: "NYL", stat: "Rebounds", line: 9.5, projection: 8.1, bookOdds: +105, trueOdds: 58.1, edge: 4.2, isOver: false },
    { id: 3, player: "Jewell Loyd", team: "SEA", stat: "Assists", line: 4.5, projection: 5.4, bookOdds: -115, trueOdds: 60.5, edge: 5.1, isOver: true }
  ];

  return (
    <div className="min-h-screen bg-background pb-20">
      <Header />
      
      <main className="max-w-md mx-auto px-4 mt-6">
        <div className="mb-6">
          <h2 className="text-2xl font-bold text-white mb-1">Highest Edges</h2>
          <p className="text-sm text-slate-400">Consensus ensemble vs book lines</p>
        </div>

        <div className="flex flex-col gap-4">
          {mockProps.map(prop => (
            <EdgeCard key={prop.id} {...prop} />
          ))}
        </div>

        <AlertFeed />
      </main>
    </div>
  );
}

export default App;
