# Roadmap 🗺️

The project started as a tribute to BROK: The Investigator, a game which brought me joy and nostalgia towards classic Point & Click adventures.

The translation started on 30.06.2026 and is still ongoing. I plan to finish it by 31.10.2026, but it depends on my schedule.

More detailed information can be seen in the diagram.

```mermaid
flowchart TB
    subgraph Row1["Phase 1: Translation"]
        direction LR
        A["<b>30.06.2026</b><br>Started Translation<br>"]
        B["Creating the Translation Guide 🧾<br>(updated regularly)"]
        C["<b>31.08.2026</b><br>Goal: 50% BROK translation"]
        D["<b>21.09.2026</b><br>Goal: 75% BROK translation"]
        E["<b>12.10.2026</b><br>Goal: 100% BROK translation"]
        A --> B --> C --> D --> E
    end

    subgraph Row2["Phase 2: Editing & Proof-Reading"]
        direction LR
        F["<b>19.10.2026</b><br>Editing (unresolved questions etc.)<br>`Proof-Reading: `automated checks for grammar/inconsistencies"]
        G["<b>31.10.2026</b><br>Proof-Reading: playthrough + final edits"]
        H["Publishing 🎉"]
        F --> G --> H
    end

    I["<b>Next steps</b><br>Translating the BROKVN Engine and Natal Tail 🐊"]

    E --> F
    H --> I

    classDef done fill:#b2ceeb,stroke:#5b7fa6,stroke-width:1px,color:#1a1a1a
    classDef todo fill:#e0e0e0,stroke:#8a8a8a,stroke-width:1px,color:#1a1a1a

    class A,B,C done
    class D,E,F,G,H,I todo
```
