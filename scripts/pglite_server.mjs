// Lightweight PostgreSQL for the integration tests without Docker:
//   npm install @electric-sql/pglite @electric-sql/pglite-socket
//   node scripts/pglite_server.mjs &
//   DBPARITY_PG_DSN="host=127.0.0.1 port=5433 user=postgres dbname=postgres" pytest tests/test_postgres_integration.py
// PGlite limitation: one connection at a time.
import { PGlite } from '@electric-sql/pglite';
import { PGLiteSocketServer } from '@electric-sql/pglite-socket';

const db = await PGlite.create();
const server = new PGLiteSocketServer({ db, port: 5433, host: '127.0.0.1' });
await server.start();
console.log('PGlite ready on 127.0.0.1:5433');
