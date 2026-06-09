import React from 'react';
import { Header } from './components/Header';
import { EdgeCard } from './components/EdgeCard';
import { AlertFeed } from './components/AlertFeed';
import ExecutionMonitor from './components/ExecutionMonitor';

function App() {
  return (
    <div className="min-h-screen p-4 md:p-8 flex flex-col items-center">
      <Header />
      
      <main className="w-full max-w-6xl mt-8 grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* Left Column: Alerts & Flow */}
        <div className="lg:col-span-1 space-y-6">
          <AlertFeed />
        </div>
        
        {/* Middle Column: Edge Dashboard */}
        <div className="lg:col-span-1 space-y-6 flex flex-col items-center">
          <div className="w-full max-w-md mb-2 flex justify-between items-center">
             <h2 className="text-xl font-bold text-white tracking-wide">Top Edges</h2>
             <span className="bg-white/10 text-xs px-2 py-1 rounded text-gray-300">Agent 3</span>
          </div>
          <EdgeCard 
            player="Breanna Stewart"
            stat="Points"
            line={22.5}
            bookOdds={-110}
            projection={26.4}
            trueOdds={65.2}
            edge={12.5}
            isOver={true}
            team="NYL"
          />
          <EdgeCard 
            player="A'ja Wilson"
            stat="Rebounds"
            line={10.5}
            bookOdds={105}
            projection={12.1}
            trueOdds={58.2}
            edge={8.2}
            isOver={true}
            team="LVA"
          />
        </div>

        {/* Right Column: Execution & Bankroll */}
        <div className="lg:col-span-1 space-y-6">
           <ExecutionMonitor />
        </div>

      </main>
    </div>
  );
}

export default App;
