import { createClient } from 'redis';

async function runBenchmark() {
  const client = createClient();
  await client.connect();

  const channels = [
    'channel_ev_alerts',
    'channel_steam_alerts',
    'channel_approved_edges',
    'channel_roster_updates',
    'channel_referee_context',
    'channel_sentiment_context'
  ];

  console.log(`Benchmarking Redis subscriptions for ${channels.length} channels...`);

  // --- Sequential Benchmark ---
  const seqSubscriber = client.duplicate();
  await seqSubscriber.connect();

  const seqStart = process.hrtime.bigint();
  for (const channel of channels) {
    await seqSubscriber.subscribe(channel, () => {});
  }
  const seqEnd = process.hrtime.bigint();
  const seqDurationMs = Number(seqEnd - seqStart) / 1_000_000;

  await seqSubscriber.quit();

  // --- Parallel Benchmark ---
  const parSubscriber = client.duplicate();
  await parSubscriber.connect();

  const parStart = process.hrtime.bigint();
  await Promise.all(
    channels.map(channel => parSubscriber.subscribe(channel, () => {}))
  );
  const parEnd = process.hrtime.bigint();
  const parDurationMs = Number(parEnd - parStart) / 1_000_000;

  await parSubscriber.quit();
  await client.quit();

  console.log('\n--- Benchmark Results ---');
  console.log(`Sequential: ${seqDurationMs.toFixed(2)} ms`);
  console.log(`Parallel:   ${parDurationMs.toFixed(2)} ms`);
  console.log(`Improvement: ${(seqDurationMs - parDurationMs).toFixed(2)} ms (${((seqDurationMs - parDurationMs) / seqDurationMs * 100).toFixed(2)}% faster)`);
}

runBenchmark().catch(console.error);
