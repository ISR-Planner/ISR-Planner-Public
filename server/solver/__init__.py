"""Solver package.

Import concrete solver helpers from their owning modules instead of relying on
package-level re-exports. Keeping this initializer lightweight avoids loading
the full solver/optimizer stack during simple imports and type checks.
"""
