export const REPO_URL = 'https://github.com/Donkey-Development-Kit/donkey-development-kit'

// The ref every reader-facing link into the SDK repository points at. The site
// is built from `main` (.github/workflows/docs.yml) and the PyPI page describes
// the released package, so a `develop` link would show unreleased code. MDX
// links can't interpolate, so pages spell the URL out; repo.test.mjs (run by
// `npm test` in CI) fails on any SDK link in content/, components/ or the PyPI
// README that names another ref, or on a relative link in the PyPI README.
export const DOCS_REF = 'main'

// The one deliberate exception: "Edit this page" opens GitHub's editor, which
// proposes the change against the ref in the URL. Contributions target
// `develop`; `main` only moves on promotion.
export const EDIT_REF = 'develop'
