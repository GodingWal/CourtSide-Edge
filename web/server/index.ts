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
