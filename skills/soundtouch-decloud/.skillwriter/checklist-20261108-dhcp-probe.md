# skill-writer checklist - soundtouch-decloud (soundtouch_dhcp.py in the Scripts section)

- [x] Change: SKILL.md Scripts section names `soundtouch_dhcp.py` as a second launch exception
      (`sudo python3`, root, no `uv` on root's PATH) and adds its table row.
- [x] Receipt held; the editing agent loaded the skill through the Skill tool before the edit.
- [x] RED (opus, retrieval): given the pre-edit Scripts section and an owner asking whether a
      static address lies outside the router's DHCP range with no router login, the probe
      answered NONE for both the row and the launch line.
- [x] GREEN (opus and haiku, same scenario, edited text pasted in): both quoted the
      `soundtouch_dhcp.py` row and the `sudo python3 scripts/soundtouch_dhcp.py` launch line, and
      named "Linux only" and root as the conditions.
- [x] GREEN diffed against RED: nothing RED produced is missing; RED's own advice (do not guess
      the router password, read the range from the router) is in service-setup.md's subsection.
- [x] Tiers opus and haiku, one cell each for GREEN, one opus cell for RED.
- [x] Every command the change adds was executed: the gates (pytest, pyright strict, check_repo)
      pass, the contract test parses the docstring and reference usage lines, and the probe's
      verdict paths are driven through `run` with a stand-in transport.
- [x] Width and fence check over the added lines of every touched Markdown file printed nothing.
- [x] Skill gaps triaged:
  - Closed in code: an address on a different network than the one the DHCP server serves read
    as `outside` (the server offers from its own pool, so no neighbour comes back). The probe now
    reads the subnet mask from the offers and answers `other-subnet` (exit 1); tests and a
    mutation battery (4 of 4 arms killed) pin it.
  - Closed in text: service-setup.md now names every verdict with its exit code, including
    `other-subnet` and `no-answer`, and says to read the range from the router without root.
  - Declined, covered elsewhere: subcommand and flags, what the probe sends (DISCOVER only, never
    a lease), the meaning of exit 0 (outside AND free), and which interface to use are in
    references/service-setup.md, "A static address must lie outside the router's DHCP range".
  - Declined, covered elsewhere: the documentation address 192.0.2.10 is a placeholder; the same
    file says "`192.0.2.10` stands for the pinned address from phase 2".
  - Declined, out of scope: the working directory for `scripts/...` is the same for every script
    in the table and is not specific to this one.
- [x] Every address added is a reserved documentation value (192.0.2.0/24, 203.0.113.0/24).
- Decisions: none - this skill keeps no decision ledger; the probe's design choices are in the
  script's module docstring.
