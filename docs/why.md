# Why this app exists

A plain-English explainer. If the technical README has you reaching for caffeine,
read this first.

## The problem in one sentence

Your church wants to know how many people came to service. For a large venue,
doing it manually is unreliable — and "just point a camera at it" sounds easy
but is full of traps.

## Why naive solutions break

- **Clicker at the door.** Works for 50 people. With 500+ across multiple doors,
  the volunteer gets tired, miscounts, misses late arrivals, and you can't put
  one at every entrance.
- **Counting empty seats.** Ignores people standing in the back, kids on laps,
  the foyer.
- **Eyeballing.** Every pastor's number is suspiciously round.
- **One camera, one tally.** The moment you have two cameras (or two doors),
  you don't know if you're seeing the same person twice.

## What's actually hard about counting people from video

1. **A person isn't one frame, it's hundreds.** Walking through a doorway takes
   maybe a second — that's 30 video frames. Without tracking, naïve counting
   would say 30 people walked through. The detector has to stitch those frames
   into "this is one person, named #4."

2. **People look different in every frame.** Their pose changes, they get
   partly hidden behind others, lighting shifts. The system has to recognise
   that the half-occluded person at frame 100 is the same as the fully-visible
   one at frame 80. (That's what the BoT-SORT + ReID tracker does — it
   remembers each person's appearance to bridge brief occlusions.)

3. **Direction matters.** "How many entered?" is different from "how many
   crossed the line?" You draw a virtual line at the doorway and the app only
   counts a person when their feet cross it — and remembers which side they
   came from.

4. **Back-and-forth is real.** A parent stepping out with a crying baby and
   coming back is genuinely 1-in, 1-out, 1-in. Three events. Naïve "dedupe"
   would have called that one count, and you'd be wrong.

5. **Multiple cameras = double counting if you're not careful.** Two cameras
   pointed at the same main entrance from different angles will each say
   "I saw 100 people enter." That's not 200 people. This is the hardest part
   of the whole thing.

## How the app handles the big one — multiple cameras

You tell each camera where it is in the actual room by clicking 4+ matching
points: "this corner of the rug in the camera view is *here* on the floor plan,
in metres." Once two cameras share a floor plan, the app can tell — when both
see a person at world position (7.5 m, 5 m) within a 3-second window — that
it's the same human. Only one count goes through; the other is silently
flagged as a duplicate so you can still see what was deduped in the audit log.

For large churches with multiple cameras at the same entrance (or coverage
that overlaps), this is the difference between "267 people in the sanctuary"
and "534 people in the sanctuary, lol no there are 267 again."

## What you actually get

A live dashboard that says, for every service:

> Sanctuary: **267 inside**, **312 entered**, **45 left**, peak **269** at 10:47.

Plus a session log you can export as CSV — every entrance, every exit,
timestamped, with which camera and which doorway. Pastor wants to know if
9 a.m. or 11 a.m. is growing? Now you can answer that with data, not vibes.

## When this is overkill

For a 50-person chapel, a clicker still wins. The setup time and the cost of
running cameras isn't worth it.

## When this earns its keep

Multi-door, multi-service venues where attendance trends actually drive
decisions:

- Do we add a service?
- Is the new sermon series moving the needle?
- Is the youth crowd growing or shrinking?
- Are we hitting fire-code occupancy in peak season?
- Should we plant a campus?

This is the difference between guessing and knowing.
