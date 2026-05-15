# Web Dashboard Visual MVP Design

## Goal

Build the first local YC Hunter web interface as a visual-only dashboard shell. It must not replace the Telegram bot yet and must not start hunts. The first slice focuses on the overview experience: accounts, active hunts, cloud status, latest matches, and system metrics.

## Visual Direction

Use the approved "black glass, neon depth" direction:

- almost-black background and surfaces;
- dark translucent glass material, not gray/milky panels;
- glow outside/behind surfaces rather than bright surfaces;
- purple, blue, and pink ambient bloom;
- subtle noise texture and specular highlights;
- SF Pro on Apple systems with Manrope fallback;
- responsive layout for desktop, laptop, and phone.

## Local Scope

Create a separate `frontend/` Vite React app. Use static typed mock data for this visual slice. The next backend slice will connect the UI to FastAPI endpoints over the existing scheduler/database layer.

## Screens

The MVP screen is a dashboard cockpit with:

- left navigation on desktop;
- compact navigation on mobile;
- hero metrics;
- current hunt progress;
- cloud overview table;
- latest matches panel;
- account health and VM config summary.

## Out Of Scope

- Starting hunts from web.
- Editing accounts from web.
- Authentication.
- FastAPI backend implementation.
- Telegram bot changes.
