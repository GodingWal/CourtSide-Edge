import express from 'express';
import cors from 'cors';

const app = express();
const port = process.env.PORT || 3001;

app.use(cors());
app.use(express.json());

// Dummy endpoint for Edge Cards
app.get('/api/edges', (req, res) => {
  res.json([
    { id: 1, player: "A'ja Wilson", team: "LVA", stat: "Points", line: 22.5, projection: 24.8, bookOdds: -110, trueOdds: 62.4, edge: 6.8, isOver: true },
    { id: 2, player: "Breanna Stewart", team: "NYL", stat: "Rebounds", line: 9.5, projection: 8.1, bookOdds: 105, trueOdds: 58.1, edge: 4.2, isOver: false },
  ]);
});

app.listen(port, () => {
  console.log(`CourtSideEdge Server running on port ${port}`);
});
