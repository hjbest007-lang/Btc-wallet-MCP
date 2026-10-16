# Disclaimer

wallet-mcp is experimental software. Read this before you install it, point an
AI agent at it, or move any value with it.

## English

**Experimental and unaudited.** This project has not had an independent
security audit. It is research and operator tooling, not a custodial product
and not a finished wallet.

**Use at your own risk.** You decide whether to run it, which coins to enable,
which networks to use, and how much value to place in a hot wallet.

**No warranty.** The software is provided "AS IS", without warranty of any
kind, under the [MIT license](LICENSE). That license disclaims implied
warranties of merchantability, fitness for a particular purpose, and
non-infringement.

**Not advice.** Nothing in this repository, its tools, its prompts, or its
output is financial, investment, legal, or tax advice. A payment an agent
prepares is not a recommendation.

**You are responsible.** You are solely responsible for:

- generating, storing, backing up, and protecting seeds, private keys, and the
  approval secret;
- the limits, allowlists, and networks you configure;
- every approval code you hand to an agent;
- compliance with the laws and rules that apply to you, including tax
  reporting and anti-money-laundering / know-your-customer (AML/KYC)
  obligations.

**You can lose funds.** Bugs, a mistake by the AI agent, prompt injection, a
hostile chain API, a wrong address or destination tag, or a compromised
computer, dependency, or MCP client can send value to the wrong place or
expose a hot key. The cold seed is not a guarantee if you copy it onto the
agent host. The approval gate reduces some of those risks. It does not remove
them.

**Mainnet.** Every coin defaults to a test network. Mainnet stays off until
you set that coin's `I_UNDERSTAND_REAL_MONEY` flag. Even then, use only small
amounts, and only after you have reviewed the code and the
[security model](SECURITY.md) yourself. Do not put savings in the hot wallet.

**No liability.** The authors and contributors are not liable for any claim,
damages, or other liability arising from the software or from your use of it,
to the extent stated in the MIT license. That includes loss of funds, failed
or unexpected transactions, downtime, and any regulatory consequence of using
the software.

If you do not accept this, do not run wallet-mcp.

## Nederlandse samenvatting

Experimentele, niet-geauditeerde software. Gebruik op eigen risico. Geen
garantie; de MIT-licentie sluit aansprakelijkheid uit. Dit is geen financieel,
juridisch of fiscaal advies. Jij bent zelf verantwoordelijk voor sleutels,
seeds, het approval-secret, limieten en voor naleving van lokale wet- en
regelgeving, onder meer belasting en AML/KYC. Verlies van funds is mogelijk
door bugs, fouten van de AI-agent, prompt-injectie of gecompromitteerde
systemen. Mainnet alleen met kleine bedragen en pas na je eigen review.
Auteurs en contributors zijn niet aansprakelijk.
