import express from 'express';
import { createClient } from 'redis';
import cors from 'cors';
import { db } from './db';
import { players, bankroll_history } from './schema';
import { desc } from 'drizzle-orm';

const app = express();
const port = 3000;

app.use(cors());

const redisClient = createClient({
    url: 'redis://localhost:6379'
});

redisClient.on('error', err => console.log('Redis Client Error', err));

// Standard REST Endpoints
app.get('/api/bankroll/history', async (req, res) => {
    try {
        const history = await db.query.bankroll_history.findMany({
            orderBy: [desc(bankroll_history.timestamp)],
            limit: 100
        });
        res.json(history);
    } catch (err) {
        res.status(500).json({ error: 'Failed to fetch bankroll history' });
    }
});

app.get('/api/players/active', async (req, res) => {
    try {
        const activePlayers = await db.query.players.findMany({
            where: (players, { eq }) => eq(players.status, 'ACTIVE')
        });
        res.json(activePlayers);
    } catch (err) {
        res.status(500).json({ error: 'Failed to fetch players' });
    }
});

// Agent 13 Proxy Routes
app.get('/api/matchup/:player/:team', async (req, res) => {
    try {
        // Proxy to Agent 13 container (assuming localhost:8009 for local dev)
        const response = await fetch(`http://localhost:8009/api/matchup/${req.params.player}/${req.params.team}`);
        const data = await response.json();
        res.json(data);
    } catch (err) {
        res.status(500).json({ error: 'Failed to reach Agent 13' });
    }
});

app.post('/api/custom_prop', express.json(), async (req, res) => {
    try {
        const response = await fetch(`http://localhost:8009/api/custom_prop`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(req.body)
        });
        const data = await response.json();
        res.json(data);
    } catch (err) {
        res.status(500).json({ error: 'Failed to reach Agent 13' });
    }
});

// SSE Endpoint for Alerts
app.get('/api/stream/alerts', async (req, res) => {
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');

    // Send an initial heartbeat
    res.write(': heartbeat\n\n');

    try {
        const subscriber = redisClient.duplicate();
        await subscriber.connect();

        const channels = [
            'channel_ev_alerts', 
            'channel_steam_alerts', 
            'channel_approved_edges',
            'channel_roster_updates'
        ];

        for (const channel of channels) {
            await subscriber.subscribe(channel, (message) => {
                res.write(`data: ${JSON.stringify({ channel, message })}\n\n`);
            });
        }

        req.on('close', () => {
            subscriber.unsubscribe();
            subscriber.quit();
        });
    } catch (err) {
        console.error('SSE Subscription Error:', err);
        res.end();
    }
});

async function start() {
    await redisClient.connect();
    app.listen(port, () => {
        console.log(`Server running on port ${port}`);
    });
}

start().catch(console.error);
