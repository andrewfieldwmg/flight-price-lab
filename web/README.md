# Flight Price Lab Web

Next.js App Router frontend for the Flight Price Lab FastAPI service.

```powershell
copy .env.example .env.local
npm install
npm run dev
```

The app runs at `http://localhost:3000`; start the backend separately at
`http://localhost:8000`. Calendar fare previews degrade gracefully because the current
backend calendar provider adapter is intentionally unavailable.
