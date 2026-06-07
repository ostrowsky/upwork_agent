"""Seed the case library with portfolio cases for the Unity/mobile-games niche.

Idempotent: skips a case if one with the same title already exists.
Content is operator-provided portfolio (responsibility of the account owner,
per docs/specs/product-map.md). Run: python seed_cases.py
"""
from __future__ import annotations

from database import init_db, get_db_session, CaseStudy


CASES = [
    {
        "title": "Real-time multiplayer arena shooter (mobile)",
        "niche": "Unity multiplayer mobile game",
        "stack": "Unity, Photon Fusion, C#, Firebase, PlayFab",
        "budget_range": "$18k-$25k",
        "url": "https://crocoapps.com/portfolio/arena-shooter",
        "description": (
            "Built a cross-platform (iOS/Android) real-time PvP arena shooter for an "
            "indie publisher. Implemented authoritative server logic on Photon Fusion, "
            "client-side prediction and lag compensation, skill-based matchmaking, and "
            "a lobby/party system. Integrated Firebase auth and PlayFab economy."
        ),
        "result": "Shipped in 14 weeks; <90ms average match latency; 38% D7 retention at soft launch.",
    },
    {
        "title": "Co-op survival prototype to MVP (Quest + PC)",
        "niche": "Unity multiplayer co-op",
        "stack": "Unity, Netcode for GameObjects, C#, Steamworks",
        "budget_range": "$12k-$20k",
        "url": "https://crocoapps.com/portfolio/coop-survival",
        "description": (
            "Took a 2-4 player co-op survival concept from prototype to a vertical-slice "
            "MVP. Built session/lobby flow, host-migration-safe state sync with Unity "
            "Netcode, inventory and crafting replication, and a class-based loadout system."
        ),
        "result": "Delivered playable MVP in 9 weeks; client closed a publisher deal off the slice.",
    },
    {
        "title": "Hyper-casual mobile game: 0 to 1M installs",
        "niche": "Unity casual mobile game",
        "stack": "Unity, C#, DOTween, AdMob, Firebase Analytics",
        "budget_range": "$5k-$10k",
        "url": "https://crocoapps.com/portfolio/hypercasual-corner",
        "description": (
            "Designed and built a polished physics-based hyper-casual game (swipe-to-launch "
            "mechanic) with 30 handcrafted levels, juicy feedback, and ad/IAP monetization. "
            "Tuned for 60fps on low-end Android devices."
        ),
        "result": "1.1M installs in first 3 months; $0.21 ARPDAU; featured in casual category.",
    },
    {
        "title": "Photon matchmaking + lobby system as a reusable module",
        "niche": "Unity multiplayer infrastructure",
        "stack": "Unity, Photon (PUN2 & Fusion), C#",
        "budget_range": "$6k-$12k",
        "url": "https://crocoapps.com/portfolio/photon-lobby-kit",
        "description": (
            "Built a drop-in matchmaking, party, and lobby module for a studio reusing it "
            "across three titles: ranked/quick-play queues, reconnection handling, region "
            "selection, and a friend-invite flow. Documented and unit-tested."
        ),
        "result": "Reused across 3 shipped games; cut multiplayer setup time per project by ~40%.",
    },
    {
        "title": "Mobile game UI implementation from Figma (UI kit)",
        "niche": "Unity mobile game UI",
        "stack": "Unity UGUI, C#, Figma, TextMeshPro",
        "budget_range": "$4k-$8k",
        "url": "https://crocoapps.com/portfolio/mobile-ui-kit",
        "description": (
            "Implemented a full mobile game UI from a Figma UI kit: home, shop, settings, "
            "match HUD, and reward screens. Built responsive layouts for notch/safe-area, "
            "reusable prefab components, and smooth tween-based transitions handed off to "
            "the gameplay engineers."
        ),
        "result": "Pixel-accurate handoff; engineers integrated screens with zero rework.",
    },
    {
        "title": "VR multiplayer shooter prototype (Meta Quest 3)",
        "niche": "Unity VR multiplayer",
        "stack": "Unity, XR Interaction Toolkit, Photon Fusion, C#",
        "budget_range": "$15k-$22k",
        "url": "https://crocoapps.com/portfolio/vr-quest-shooter",
        "description": (
            "Built a proof-of-concept multiplayer VR shooter for Meta Quest 3: networked "
            "hand/weapon interactions, synced physics for projectiles, comfort options, and "
            "a 2-team round loop. Optimized for the Quest 3 mobile GPU budget."
        ),
        "result": "Stable 72fps on-device; investor demo greenlit full production.",
    },
]


def seed() -> dict:
    init_db()
    db = get_db_session()
    added = skipped = 0
    try:
        for c in CASES:
            exists = db.query(CaseStudy).filter(CaseStudy.title == c["title"]).first()
            if exists:
                skipped += 1
                continue
            db.add(CaseStudy(**c))
            added += 1
        db.commit()
    finally:
        db.close()
    return {"added": added, "skipped": skipped}


if __name__ == "__main__":
    result = seed()
    print(f"Seeded cases — added: {result['added']}, skipped (already present): {result['skipped']}")
