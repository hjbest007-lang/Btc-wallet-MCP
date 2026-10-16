"""Operator-facing warnings. Not a substitute for DISCLAIMER.md.

Shown by ``wallet-mcp configure`` and when a mainnet coin is enabled.
The approval secret is never part of these strings.
"""

SHORT_WARNING = (
    "WARNING: experimental, unaudited software. Use at your own risk. "
    "No warranty (see the MIT license). Not financial, legal, or tax advice. "
    "You are responsible for keys, seeds, limits, and compliance with local law, "
    "including tax and AML/KYC. Funds can be lost to bugs, AI-agent mistakes, "
    "prompt injection, or a compromised system. "
    "Mainnet only with small amounts and only after your own review. "
    "Authors and contributors are not liable. See DISCLAIMER.md."
)

DUTCH_SUMMARY = (
    "Nederlandse samenvatting: experimentele, niet-geauditeerde software; "
    "gebruik op eigen risico; geen garantie (MIT-licentie); geen financieel, "
    "juridisch of fiscaal advies; jij bent zelf verantwoordelijk voor sleutels, "
    "seeds, limieten en naleving van lokale wet- en regelgeving (onder meer "
    "belasting en AML/KYC); verlies van funds is mogelijk door bugs, fouten van "
    "de AI-agent, prompt-injectie of gecompromitteerde systemen; mainnet alleen "
    "met kleine bedragen en na je eigen review; auteurs en contributors zijn "
    "niet aansprakelijk."
)

MAINNET_WARNING = (
    "WARNING: mainnet moves real value. This software is experimental and "
    "unaudited. Use only small amounts, and only after your own review. "
    "You remain responsible for keys, limits, and legal compliance. "
    "No warranty; authors and contributors are not liable. See DISCLAIMER.md."
)


def configure_banner() -> str:
    return SHORT_WARNING + "\n" + DUTCH_SUMMARY
