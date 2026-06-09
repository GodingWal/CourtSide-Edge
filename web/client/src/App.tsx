import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import MarketDivergence from './views/MarketDivergence';
import AlphaSandbox from './views/AlphaSandbox';
import BankrollDiagnostics from './views/BankrollDiagnostics';
import IntelligenceFeed from './views/IntelligenceFeed';

function App() {
  return (
    <Router>
      <div className="min-h-screen bg-zinc-950 flex font-sans text-zinc-100">
        <Sidebar />
        <main className="flex-1 overflow-auto">
           <Routes>
              <Route path="/" element={<MarketDivergence />} />
              <Route path="/sandbox" element={<AlphaSandbox />} />
              <Route path="/diagnostics" element={<BankrollDiagnostics />} />
              <Route path="/intelligence" element={<IntelligenceFeed />} />
           </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;
