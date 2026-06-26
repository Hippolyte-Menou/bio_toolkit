"""
SVG diagram highlighting.

Loads a base SVG cell/organelle diagram, highlights a set of named sub-elements
(by ``<g id=...>``), extracts their descriptions, and writes the modified SVG to
an output directory.

Ported from the vault's ``gene-generation/svg_diagram.py``. The original pulled
the base-diagram registry, stroke/fill colours, and output directory from
``settings.GeneConfig`` / ``settings.DataPaths``; here those are explicit
constructor arguments so the highlighter is reusable outside the vault.

``bs4`` (BeautifulSoup) is imported lazily inside :meth:`_load_svg` so importing
this module does not require ``beautifulsoup4`` / ``lxml`` to be installed.
"""

import os
from typing import List, Optional, Tuple

# Original defaults from settings.GeneConfig -- kept as module constants so a
# caller that does not pass overrides reproduces the vault's styling.
DEFAULT_STROKE_COLOUR = "#5e5e5e"
DEFAULT_FILL_COLOUR = "#e6dab3"


class EnhancedSVGDiagram:
    """Highlight named elements in a base SVG diagram for one gene.

    Parameters
    ----------
    base_diagram:
        Key into *svg_paths* identifying which base diagram to load.
    elements_to_highlight:
        Element IDs (``<g id=...>``) to highlight. Hyphens are stripped to match
        the base SVG's id convention.
    gene_symbol:
        Used in the output filename (``{gene_symbol}_{base_diagram}.svg``).
    svg_paths:
        Mapping of base-diagram key -> filesystem path of the base SVG.
        (Was ``GeneConfig.SVG_PATHS``.)
    output_dir:
        Directory the modified SVG is written to. (Was ``DataPaths.SVG_OUTPUT_DIR``.)
    vault_relative_prefix:
        Prefix used to build the returned vault-relative path. The original
        hard-coded ``_assets/images/genes_svg``; defaults to that for parity.
    stroke_colour / fill_colour:
        Highlight styling. (Were ``GeneConfig.SVG_STROKE_COLOUR`` / ``SVG_FILL_COLOUR``.)
    """

    def __init__(
        self,
        base_diagram: str,
        elements_to_highlight: List[str],
        gene_symbol: str,
        svg_paths: dict,
        output_dir: str,
        vault_relative_prefix: str = "_assets/images/genes_svg",
        stroke_colour: str = DEFAULT_STROKE_COLOUR,
        fill_colour: str = DEFAULT_FILL_COLOUR,
    ):
        self.base_diagram = base_diagram
        self.svg_paths = svg_paths
        self.base_diagram_path = svg_paths.get(base_diagram)
        self.elements_to_highlight = self._clean_element_ids(elements_to_highlight)
        self.gene_symbol = gene_symbol
        self.stroke_colour = stroke_colour
        self.fill_colour = fill_colour
        self.output_dir = output_dir
        self.vault_relative_prefix = vault_relative_prefix.rstrip("/")
        self.output_path = self._generate_output_path()
        self.descriptions = []

    def _clean_element_ids(self, elements: List[str]) -> List[str]:
        """Clean element IDs by removing hyphens and other characters"""
        return [element.replace("-", "") for element in elements if element]

    def _generate_output_path(self) -> str:
        """Generate output path for the modified SVG"""
        os.makedirs(self.output_dir, exist_ok=True)
        filename = f"{self.gene_symbol}_{self.base_diagram}.svg"
        self.vault_relative_path = f"{self.vault_relative_prefix}/{filename}"
        return os.path.join(self.output_dir, filename)

    def _validate_inputs(self) -> Tuple[bool, str]:
        """Validate input parameters"""
        if not self.base_diagram_path:
            return False, f"Unknown base diagram: {self.base_diagram}"

        if not os.path.exists(self.base_diagram_path):
            return False, f"Base diagram file not found: {self.base_diagram_path}"

        if not self.elements_to_highlight:
            return False, "No elements specified for highlighting"

        if not self.gene_symbol:
            return False, "Gene symbol is required"

        return True, "Valid inputs"

    def _load_svg(self):
        """Load and parse the SVG file.

        ``bs4`` is imported here (lazily) so the module imports without it.
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError as e:
            print(f"Error loading SVG file: BeautifulSoup (bs4) not installed: {e}")
            return None
        try:
            with open(self.base_diagram_path, "r", encoding="utf-8") as f:
                return BeautifulSoup(f.read(), "xml")
        except Exception as e:
            print(f"Error loading SVG file: {e}")
            return None

    def _extract_descriptions(self, soup) -> List[str]:
        """Extract descriptions from highlighted elements"""
        descriptions = []

        for element_id in self.elements_to_highlight:
            element = soup.find("g", {"id": element_id})
            if element:
                desc_element = element.find("text", {"class": "subcell_description"})
                if desc_element and desc_element.text:
                    descriptions.append(desc_element.text.strip())

        return descriptions

    def _highlight_elements(self, soup) -> int:
        """Highlight specified elements in the SVG"""
        highlighted_count = 0
        for element_id in self.elements_to_highlight:
            element = soup.find("g", {"id": element_id})
            if element:
                # Find all paths with class "coloured" and modify them
                coloured_paths = element.find_all("path", {"class": " coloured"})
                if rect := element.find("rect"):
                    coloured_paths.append(rect)
                for path in coloured_paths:
                    path["fill"] = self.fill_colour
                    path["stroke"] = self.stroke_colour
                    highlighted_count += 1

                # Also try to find paths with class containing "coloured"
                if not coloured_paths:
                    all_paths = element.find_all("path")
                    for path in all_paths:
                        if path.get("class") and "coloured" in " ".join(path["class"]):
                            path["fill"] = self.fill_colour
                            path["stroke"] = self.stroke_colour
                            highlighted_count += 1

        return highlighted_count

    def _save_svg(self, soup) -> bool:
        """Save the modified SVG to file"""
        try:
            with open(self.output_path, "w", encoding="utf-8") as f:
                f.write(str(soup))
            return True
        except Exception as e:
            print(f"Error saving SVG file: {e}")
            return False

    def highlight_svg(self) -> Tuple[str, str]:
        """
        Main method to highlight SVG elements
        Returns: (output_path, status_message)
        """
        # Validate inputs
        is_valid, validation_message = self._validate_inputs()
        if not is_valid:
            return "", f"Validation failed: {validation_message}"

        # Load SVG
        soup = self._load_svg()
        if not soup:
            return "", "Failed to load SVG file"

        # Extract descriptions before highlighting
        self.descriptions = self._extract_descriptions(soup)

        # Highlight elements
        highlighted_count = self._highlight_elements(soup)

        if highlighted_count == 0:
            return "", f"No elements were highlighted. Available elements might not match: {self.elements_to_highlight}"

        # Save modified SVG
        if not self._save_svg(soup):
            return "", "Failed to save modified SVG"

        status_message = f"Successfully highlighted {highlighted_count} elements"
        if self.descriptions:
            status_message += f". Found {len(self.descriptions)} descriptions"

        return self.vault_relative_path, status_message

    def get_descriptions(self) -> List[str]:
        """Get descriptions of highlighted elements"""
        return self.descriptions

    def get_element_info(self) -> dict:
        """Get information about the SVG processing"""
        return {
            "base_diagram": self.base_diagram,
            "gene_symbol": self.gene_symbol,
            "elements_to_highlight": self.elements_to_highlight,
            "output_path": self.output_path,
            "descriptions": self.descriptions,
        }
