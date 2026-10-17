# Prose

Everything written for a person to read: `docs/specs/`, `docs/notes/`, issue and
pull request bodies, the text a command prints, and the replies an agent writes
in a session.

## Do

- **Split a sentence where the reader has to hold the first idea to reach the
  second.** Two ideas in one sentence are often fine. That is the case where they
  are not.
- **Give a subject a verb.** "every thread on it the reviewer's own findings
  opened" is nouns stacked with nothing among them, and gets taken apart before
  it gets read.
- **Let a clause mean something before it ends.** "with the replies and resolved
  state of each", against "each with its replies and whether it is resolved".
- **Say the fact where a pointer would fit.** "Which threads those are is set out
  under Findings below" sends the reader away to learn something the sentence
  could have said.
- **Show output where the subject is output.** For a message, a comment or a
  command, an example usually says it faster than the description does.
- **Prefer a test to a rule.** Say how to tell that the writing is wrong, not
  which constructions to avoid.

## Never

- **Rule against a shape.** A rule against one also rules out the places it reads
  well, and prose written to satisfy a list reads like prose written to satisfy a
  list.
- **Keep a sentence whose only job is to carry another one.** Delete it and see
  whether anything is missing.

## The test

Read it once, at speed. Where you go back, the sentence is wrong rather than the
reader. Everything above is what usually fails that, for use when it fails and
the reason is not obvious.

**Run it on the finished text, as a pass of its own.** A sentence that restates
its neighbour reads well to whoever just wrote it, because the idea is already in
mind; it reads as padding only to someone meeting it cold.

## Reference: one row, before and after

Before:

> Its number, its base and head refs, its description, and every thread on it
> the reviewer's own findings opened, with the replies and resolved state of
> each. The harness fetches all of this and passes it in. Which threads those are
> is set out under Findings below.

After:

> Its number, its base and head refs, its description, and the threads the
> reviewer opened on it, each with its replies and whether it is resolved. The
> harness fetches all of this and passes it in.
