# NextUp station screens

The screen has no touch. The only input at a station is a hand held up in front of the radar for
about 2 seconds, so it works with gloves on. Output is the screen plus a speaker for chimes and
short spoken prompts.

## Station kinds

| Kind | Examples | Features on |
|---|---|---|
| Bench | Soldering bench, electronics bench, workbench | Queue, Notify, lone-worker check |
| Machine | Laser cutter, CNC, band saw (attended machines) | Queue, Notify, lone-worker check |
| Lab station | Fume hood, biosafety cabinet, microscope, microtome | Queue, Notify, lone-worker check, protocol stepper |
| Robot cell | Robot arm, pick station | Robot zone, handoff, intrusion log |

Machines that run unattended, like 3D printers, aren't a good fit: the radar sees the person, not
the machine.

## What the screen shows

Higher in the list wins when two things happen at once.

1. **"You OK?"** Full screen, a chime, and the hold ring. Raise a hand to answer.
2. **Robot zone** (robot cells only): a big green, yellow or red ring with a short label ("Robot
   slowed: person nearby").
3. **Notify pop-up:** "Someone's waiting for this station." A chime and the hold ring; raise a hand
   to say you've seen it.
4. **Out of order:** "Reported: broken. A manager has been told." The queue skips this station.
5. **Reserved:** "Reserved for the next person: 1:42", counting down the 2 minutes they have to
   claim it.
6. **Protocol step:** the current step large, the next step dimmed, and a timer for timed steps.
7. **In use:** minutes in, how many people are waiting, and a soft hint when someone is.
8. **Free:** "Free. Join the queue at labstat.us", with the lab's join code.
9. **Sensor offline:** shown when the radar stops sending frames.

## Hand-hold map

| On screen | A 2 s hand-hold does |
|---|---|
| "You OK?" | Answers "I'm OK" (`safety.ok`) |
| Notify pop-up | "I've seen it" (`notify.ack`) |
| Protocol step | Next step |
| "Start protocol?" | Starts the run |
| Robot cell, green zone | Tells the robot to start its next task |
| Anything else | Nothing |

The screen always shows the current hint ("Raise a hand to …") and fills a ring as the hold
progresses.

## Spoken prompts

Short clips made ahead of time with an offline text-to-speech tool (for example Piper). Nothing is
generated live.

- "Are you OK? Raise a hand."
- "Someone's waiting for this station."
- "This station is reserved for the next person."
- "Step complete."

## Ask the lab (website)

A chat box on the website, powered by the Gemini API. Gemini answers by calling NextUp's own read
APIs, so answers come from live data. It never reveals who is at a station or anything about safety
checks. Examples:

- "Is a soldering bench free?"
- "When is the laser cutter usually free tomorrow?"
- "How long is the wait for a fume hood?"
- "Which machines are out of order?"
