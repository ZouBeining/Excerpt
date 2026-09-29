# The Seven Rules — Rationale and Examples

Condensed from Chris Beams, "How to Write a Git Commit Message" (https://cbea.ms/git-commit/). Read when drafting a message body, or when a rule needs justification.

## Why good messages matter

- The diff tells *what*; the message tells *why*. As Peter Hutterer put it: "Re-establishing the context of a piece of code is wasteful... a commit message shows whether a developer is a good collaborator."
- A well-cared-for log makes `git blame`, `revert`, `rebase`, `log`, and `shortlog` come to life; reviewing others' commits becomes efficient and independent.
- Good logs never happen by accident — they follow a convention agreed in advance. The three things a team must agree on: **style** (markup, wrap, grammar, capitalization), **content** (what belongs in the body), and **metadata** (how issue IDs and PR numbers are referenced).

## Rule 1 — Separate subject from body with a blank line

Git's manpage: the text up to the first blank line is treated as the commit title, and that title is used throughout Git — `format-patch` puts it on the Subject line of an email, `log --oneline` and `shortlog` print only it. None of these work properly without the blank line.

A trivial change needs a subject only:

```
Fix typo in introduction to user guide
```

Nothing more need be said; if the reader wonders what the typo was, `git show` answers it.

## Rule 2 — Limit the subject to 50 characters

Not a hard limit, a rule of thumb. Keeping subjects at this length keeps them readable and forces the author to think about the most concise way to explain the change.

> If you're having a hard time summarizing, you might be committing too many changes at once. Strive for atomic commits — one logical change per commit.

GitHub warns past 50 characters and truncates past 72 with an ellipsis. Shoot for 50, treat 72 as the hard limit.

## Rule 3 — Capitalize the subject line

- ✓ Accelerate to 88 miles per hour
- ✗ ~~accelerate to 88 miles per hour~~

Conventional Commits nuance: the type/scope prefix stays lowercase because the spec requires it; the description after the colon is capitalized — `feat: Accelerate time travel`.

## Rule 4 — Do not end the subject line with a period

Trailing punctuation is unnecessary, and space is precious when the limit is 50 characters.

- ✓ Open the pod bay doors
- ✗ ~~Open the pod bay doors.~~

## Rule 5 — Use the imperative mood

Imperative = phrased as a command ("Clean your room", "Close the door"). It is the right voice for subjects because **git itself uses the imperative** whenever it creates a commit on your behalf:

```
Merge branch 'myfeature'
Revert "Add the thing with the stuff"
Merge pull request #123 from someuser/somebranch
```

The test: a properly formed subject always completes the sentence "If applied, this commit will ___".

- ✓ refactor subsystem X for readability
- ✓ update getting started documentation
- ✓ remove deprecated methods
- ✓ release version 1.0.0
- ✗ ~~fixed bug with Y~~ (indicative mood)
- ✗ ~~changing behavior of X~~ (indicative mood)
- ✗ ~~more fixes for broken stuff~~ (describes contents)
- ✗ ~~sweet new API methods~~ (describes contents)

The imperative applies only to the subject line; the body can relax.

## Rule 6 — Wrap the body at 72 characters

Git never wraps text automatically — the author must mind the right margin. Wrapping at 72 leaves room for git's own indentation while keeping the whole message under 80 characters.

## Rule 7 — Body explains what and why, not how

Explain the problem the commit solves, why this approach was chosen, and any side effects or unintuitive consequences. Leave out the *how* — the code is self-explanatory (and if it is too complex to be, that is what source comments are for). Focus on what was wrong before, how it works now, and why this solution.

The full message template:

```
Summarize changes in around 50 characters or less

More detailed explanatory text, if necessary. Wrap it to about 72
characters or so. In some contexts, the first line is treated as the
subject of the commit and the rest of the text as the body. The
blank line separating the summary from the body is critical (unless
you omit the body entirely); various tools like `log`, `shortlog`
and `rebase` can get confused if you run the two together.

Explain the problem that this commit is solving. Focus on why you
are making this change as opposed to how (the code explains that).
Are there side effects or other unintuitive consequences of this
change? Here's the place to explain them.

Further paragraphs come after blank lines.

 - Bullet points are okay, too

 - Typically a hyphen or asterisk is used for the bullet, preceded
   by a single space, with blank lines in between, but conventions
   vary here

If you use an issue tracker, put references to them at the bottom,
like this:

Resolves: #123
See also: #456, #789
```

## A real-world example

Bitcoin Core commit eb0b56b, "Simplify serialize.h's exception handling": the body explains that `exceptmask` always included `failbit` and `setstate` was always called with `bits = failbit`, so both immediately raised an exception — removing the variables and throwing directly deletes dead code, and `good()` can be replaced by `!eof()`. A reader understands the whole refactor without re-deriving it. That context, written down at commit time, would otherwise be lost forever.

**Tip**: if writing a good subject is hard because the change is large, split the commit. Atomic commits make both the diff and the message easier to write and read.
