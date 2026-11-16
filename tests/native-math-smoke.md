# Native math smoke test

Open this file in Zed's Markdown preview. Resize the preview, switch between light and dark themes, and select/copy the source across formulas.

## Inline delimiters and Chinese wrapping

中文与行内公式（$E=mc^2$）、括号公式（\(a^2+b^2=c^2\)）应自然换行。缩窄窗口后，这段文字和标点仍应留在预览区域内，且括号不应独自留在上一行或下一行。

## Display delimiters

$$
\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}
\begin{bmatrix}x \\ y\end{bmatrix}
=
\begin{bmatrix}5 \\ 11\end{bmatrix}
$$

\[
\mathcal{F}\{f(t)\}(\omega)
=
\int_{-\infty}^{\infty} f(t)e^{-i\omega t}\,dt
\]

## Lists retain their hierarchy

- Parent formula $x^2$.
  - Nested formula \(y^2\).
    - Deep child $z^2$.
  - The second child stays below the parent.
- [x] Task item with $\alpha+\beta$.

1. Numbered item with $\sum_{i=1}^{n} i$.
   - Its child remains indented.
2. Second numbered item.

## Code stays literal

`$x^2$` and `\(y^2\)` stay as code, not rendered formulas.

```latex
$$\begin{matrix}1 & 2 \\ 3 & 4\end{matrix}$$
\[a^2+b^2=c^2\]
```
