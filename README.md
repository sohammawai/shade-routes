# Shade Routes

A tree planting planner that shows cities where shade is missing on the streets people actually walk, plus a shady walking route for pedestrians.

Pilot area: Connaught Place, New Delhi.

## Progress
- [x] Step 1: Walking street network (OSMnx)
- [x] Step 2: Buildings and height estimates
- [x] Step 3: Building shadows by time of day (pvlib)
- [ ] Tree canopy and hourly shade scores
- [ ] Pedestrian demand and planting priority
- [ ] Planting simulator and shady routing
- [ ] Web app and chatbot
- [ ] Deploy on AWS

## Setup
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python step3.py