"""Helper functions for parsing and templating MESH inputs.

Provides utilities to parse CLASS and hydrology configuration blocks,
derive parameter structures, generate parameter names for templating,
and perform small text/file transformations used by the MESH builder.
"""
# built-in imports
from typing import (
    Dict,
    Union,
    List,
    Tuple,
)
from pathlib import Path

import re
import os
import sys

# NameType type alias for parameter names
NameType = Union[str, int, float]

# custom types
# PathLike type alias for file system paths
if sys.version_info >= (3, 10):
    from typing import TypeAlias
    PathLike: TypeAlias = Union[str, Path]
else:
    PathLike = Union[str, Path]

def remove_comments(
    string
) -> str:
    """Remove trailing CLASS-style comments from a string.

    Parameters
    ----------
    string : str
        Input text containing CLASS lines with trailing comment fields.

    Returns
    -------
    str
        Text with comment portions removed.
    """
    return re.sub(r'\s+\d{2}\s(?:[^\n ]| (?! ))*$', '', string, flags=re.MULTILINE)

def class_section_divide(
    section: str,
    **read_csv_kwargs
) -> Dict:
    """Split a CLASS block into named sub-sections.

    Parameters
    ----------
    section : str
        Text block for a single CLASS computational unit.
    **read_csv_kwargs
        Unused; placeholder for future parsing customizations.

    Returns
    -------
    dict
        Mapping with keys ``veg1``, ``veg2``, ``hyd1``, ``hyd2``, ``soil``,
        ``prog1``, ``prog2``, and ``prog3``.
    """
    # split lines
    lines = section.splitlines()

    # build a dictionary out of CLASS sections
    class_section = {}
    
    # vegetation parameters
    class_section['veg1'] = "\n".join(lines[:4])
    class_section['veg2'] = "\n".join(lines[4:7])
    
    # surface/hydraulic parameters
    class_section['hyd1'] = lines[7]
    class_section['hyd2'] = lines[8]

    # soil parameters
    class_section['soil'] = "\n".join(lines[9:12])

    # prognostic parameters
    class_section['prog1'] = lines[12] if len(lines[12]) > 0 else ""
    class_section['prog2'] = lines[13] if len(lines[13]) > 0 else ""
    class_section['prog3'] = lines[14] if len(lines[14]) > 0 else ""

    # return dictionary
    return class_section

def parse_class_meta_data(
    case_section : str,
) -> Dict:
    """Parse the meta-data header of a CLASS file.

    Extracts author/location info and case-level numeric metadata required by
    templating utilities.

    Parameters
    ----------
    case_section : str
        First four lines of the CLASS file header (title, author, place, case).

    Returns
    -------
    tuple
        ``(info_entry, case_entry)`` where ``info_entry`` contains author and
        location, and ``case_entry`` includes centroid coordinates, reference
        heights, and counts (``NL``, ``NM``).
    """
    # remove comments from the section
    case_section = remove_comments(case_section)
    
    # hard-coded values based on different lines of the CLASS file
    # the indices refer to line numbers in the section
    title_line = case_section.splitlines()[0]
    author_line = case_section.splitlines()[1]
    place_line = case_section.splitlines()[2]
    case_line = case_section.splitlines()[3]

    # now building dictionaries that MESHFlow needs just here for
    # simplicity
    info_entry = {
        "author": author_line.strip(),
        "location": place_line.strip(),
    }

    # now building the `case_entry` containing extra meta-data information
    # about the data

    # first stripping and splitting the `case_line` string
    case_line = case_line.strip().split()
    # build `case_entry` key-value pairs, note that the keys are hard-coded
    # to match `MESHFlow`'s requirements
    case_entry = {
        "centroid_lat": float(case_line[0]), # float value
        "centroid_lon": float(case_line[1]), # float value
        "reference_height_wndspd": float(case_line[2]), # float value
        "reference_height_spechum_airtemp": float(case_line[3]), # float value
        "reference_height_surface_roughness": float(case_line[4]), # float value
        "NL": int(case_line[-2]), # integer value, number of sub-basins
        "NM": int(case_line[-1]), # integer value, number of GRU blocks
    }
    
    return info_entry, case_entry

def determine_gru_type(
    line : str
) -> int:
    """Determine GRU type index from a CLASS vegetation header line.

    Parameters
    ----------
    line : str
        Whitespace-separated line containing GRU fractions and descriptors.

    Returns
    -------
    int or None
        1-based index of the first column with value ``1.000`` (or first
        positive when fractions sum to 1); may be ``None`` if not found.

    Raises
    ------
    ValueError
        If the line does not represent a valid CLASS GRU type (sum is 0).
    """
    tokens = line.strip().split()
    slice_len = min(5, len(tokens))
    
    # to track mixed GRU types and also 
    gru_type_sum = 0
    
    # iterate over the first line of the vegetation parameter section
    for i in range(slice_len):
        # if a distinct GRU, look for 1.000 value
        if tokens[i] == "1.000":
            return i + 1  # 1-based

        # Calculate the sum until this for loop breaks
        # or ends
        gru_type_sum += float(tokens[i])

    # FIXME: if sum equals to 1, then that means we deal with a mixed GRU
    #        type, and we will have to add the relevant feature to both
    #        MESHFlow and MESHFIAT;
    #        For now, find the first column without non-zero value
    if gru_type_sum == 1:
        for i in range(slice_len):
            if float(tokens[i]) > 0:
                return i + 1

    # Raise an error if it is not a valid CLASS field
    if gru_type_sum == 0:
        raise ValueError("Invalid CLASS GRU type")

def parse_class_veg1(
    veg_section : str,
    gru_idx : int,
) -> Dict[str, float]:
    """Parse the first vegetation parameter section of CLASS.

    Parameters
    ----------
    veg_section : str
        Four-line vegetation block.
    gru_idx : int
        1-based GRU column index.

    Returns
    -------
    dict[str, float]
        Parsed parameter values keyed by variable name.

    Raises
    ------
    ValueError
        If the section has an unexpected number of lines or index is invalid.
    """
    # the `veg_section` must only be 4 lines
    veg_lines = veg_section.splitlines()
    
    if len(veg_lines) != 4:
        raise ValueError("The vegetation section must have exactly 4 lines.")

    # gru index is the 1-based index of the GRU type
    # so the index of the first column is gru_idx - 1
    # for the 5th type, the second section of each block
    # will have a value of `0`
    idx = gru_idx - 1

    # please note that the parameters are hard-coded and match the inputs
    # of MESHFlow's `meshflow.utility.render_class_template` function.
    if 1 <= gru_idx <= 4: # non-barren-land types
        veg_params = {
            # first-line parameters of the block
            'fcan': float(veg_lines[0].strip().split()[idx]),
            'lamx': float(veg_lines[0].strip().split()[idx + 5]),
            # second-line parameters
            'lnz0': float(veg_lines[1].strip().split()[idx]),
            'lamn': float(veg_lines[1].strip().split()[idx + 5]),
            # third-line parameters
            'alvc': float(veg_lines[2].strip().split()[idx]),
            'cmas': float(veg_lines[2].strip().split()[idx + 5]),
            # fourth-line parameters
            'alic': float(veg_lines[3].strip().split()[idx]),
            'root': float(veg_lines[3].strip().split()[idx + 5]),
        }

    elif gru_idx == 5:
        veg_params = {
            # first-line parameters of the block
            'fcan': float(veg_lines[0].strip().split()[idx]),
            'lamx': 0.0,
            # second-line parameters
            'lnz0': float(veg_lines[1].strip().split()[idx]),
            'lamn': 0.0,
            # third-line parameters
            'alvc': float(veg_lines[2].strip().split()[idx]),
            'cmas': 0.0,
            # fourth-line parameters
            'alic': float(veg_lines[3].strip().split()[idx]),
            'root': 0.0,
        }

    else:
        raise ValueError("Invalid GRU index. Must be between 1 and 5.")

    return veg_params

def parse_class_veg2(
    veg_section : str,
    gru_idx : int,
) -> Dict[str, float]:
    """Parse the second vegetation parameter section of CLASS.

    Parameters
    ----------
    veg_section : str
        Three-line vegetation block.
    gru_idx : int
        1-based GRU column index.

    Returns
    -------
    dict[str, float]
        Parsed parameter values keyed by variable name.

    Raises
    ------
    ValueError
        If the section has an unexpected number of lines or index is invalid.
    """
    # the `veg_section` must only be 3 lines
    veg_lines = veg_section.splitlines()
    
    if len(veg_lines) != 3:
        raise ValueError("The vegetation section must have exactly 3 lines.")

    # gru index is the 1-based index of the GRU type
    # so the index of the first column is gru_idx - 1
    # for the 5th type, the second section of each block
    # will have a value of `0`
    idx = gru_idx - 1

    # please note that the parameters are hard-coded and match the inputs
    # of MESHFlow's `meshflow.utility.render_class_template` function.
    if 1 <= gru_idx <= 4: # non-barren-land types
        veg_params = {
            # first-line parameters of the block
            'rsmn': float(veg_lines[0].strip().split()[idx]),
            'qa50': float(veg_lines[0].strip().split()[idx + 4]),
            # second-line parameters
            'vpda': float(veg_lines[1].strip().split()[idx]),
            'vpdb': float(veg_lines[1].strip().split()[idx + 4]),
            # third-line parameters
            'psga': float(veg_lines[2].strip().split()[idx]),
            'psgb': float(veg_lines[2].strip().split()[idx + 4]),
        }

    elif gru_idx == 5:
        param_names = ['rsmn', 'qa50', 'vpda', 'vpdb', 'psga', 'psgb']
        veg_params = {k: 0.0 for k in param_names}

    else:
        raise ValueError("Invalid GRU index. Must be between 1 and 5.")

    return veg_params

def parse_class_hyd1(
    hyd_line : str,
) -> Dict[str, float]:
    """Parse the first hydrology line of the CLASS block.

    Parameters
    ----------
    hyd_line : str
        Hydrology line containing numeric parameters.

    Returns
    -------
    dict[str, float]
        Parsed hydrology parameters.
    """
    # remove comments
    hyd_line = remove_comments(hyd_line)

    # strip and split based on whitespace
    hyd_line = hyd_line.strip().split()

    # please note that the parameters are hard-coded and match the inputs
    # of MESHFlow's `meshflow.utility.render_class_template` function.
    veg_params = {
        'drn': float(hyd_line[0]),
        'sdep': float(hyd_line[1]),
        'fare': float(hyd_line[2]),
        'dd': float(hyd_line[3]),
    }

    return veg_params

def parse_class_hyd2(
    hyd_line : str,
) -> Dict[str, float]:
    """Parse the second hydrology line of the CLASS block.

    Parameters
    ----------
    hyd_line : str
        Hydrology line containing numeric parameters and a descriptor tail.

    Returns
    -------
    dict[str, float]
        Parsed hydrology parameters including ``mid`` descriptor.
    """
    # remove comments
    hyd_line = remove_comments(hyd_line)

    # strip and split based on whitespace
    hyd_line = hyd_line.strip().split()

    # please note that the parameters are hard-coded and match the inputs
    # of MESHFlow's `meshflow.utility.render_class_template` function.
    hyd_params = {
        'xslp': float(hyd_line[0]),
        'xdrainh': float(hyd_line[1]),
        'mann': float(hyd_line[2]),
        'ksat': float(hyd_line[3]),
        'mid': " ".join(hyd_line[5:])
    }

    return hyd_params

def parse_class_soil(
    soil_section : str,
) -> Dict[str, float]:
    """Parse the soil section of the CLASS block.

    Parameters
    ----------
    soil_section : str
        Three-line soil block.

    Returns
    -------
    dict[str, float]
        Parsed soil parameters for three layers.
    """

    # remove comments
    soil_section = remove_comments(soil_section)

    # strip and split based on whitespace
    soil_lines = soil_section.splitlines()

    # please note that the parameters are hard-coded and match the inputs
    # of MESHFlow's `meshflow.utility.render_class_template` function.
    soil_params = {
        # first line parameters
        'sand1': float(soil_lines[0].strip().split()[0]),
        'sand2': float(soil_lines[0].strip().split()[1]),
        'sand3': float(soil_lines[0].strip().split()[2]),
        # second line parameters
        'clay1': float(soil_lines[1].strip().split()[0]),
        'clay2': float(soil_lines[1].strip().split()[1]),
        'clay3': float(soil_lines[1].strip().split()[2]),
        # third line parameters
        'orgm1': float(soil_lines[2].strip().split()[0]),
        'orgm2': float(soil_lines[2].strip().split()[1]),
        'orgm3': float(soil_lines[2].strip().split()[2]),
    }

    return soil_params

def parse_class_prog1(
    prog_line : str,
) -> Dict[str, float]:
    """Parse the first prognostic line of the CLASS block.

    Parameters
    ----------
    prog_line : str
        Prognostic line containing numeric parameters.

    Returns
    -------
    dict[str, float]
        Parsed prognostic parameters.
    """
    # remove comments
    prog_line = remove_comments(prog_line)

    # strip and split based on whitespace
    prog_line = prog_line.strip().split()

    # please note that the parameters are hard-coded and match the inputs
    # of MESHFlow's `meshflow.utility.render_class_template` function.
    prog_params = {
        'tbar1': float(prog_line[0]),
        'tbar2': float(prog_line[1]),
        'tbar3': float(prog_line[2]),
        'tcan': float(prog_line[3]),
        'tsno': float(prog_line[4]),
        'tpnd': float(prog_line[5]),
    }

    return prog_params

def parse_class_prog2(
    prog_line : str,
) -> Dict[str, float]:
    """Parse the second prognostic line of the CLASS block.

    Parameters
    ----------
    prog_line : str
        Prognostic line containing numeric parameters.

    Returns
    -------
    dict[str, float]
        Parsed prognostic parameters.
    """
    # remove comments
    prog_line = remove_comments(prog_line)

    # strip and split based on whitespace
    prog_line = prog_line.strip().split()

    # please note that the parameters are hard-coded and match the inputs
    # of MESHFlow's `meshflow.utility.render_class_template` function.
    prog_params = {
        'thlq1': float(prog_line[0]),
        'thlq2': float(prog_line[1]),
        'thlq3': float(prog_line[2]),
        'thic1': float(prog_line[3]),
        'thic2': float(prog_line[4]),
        'thic3': float(prog_line[5]),
        'zpnd': float(prog_line[6]),
    }

    return prog_params

def parse_class_prog3(
    prog_line : str,
) -> Dict[str, float]:
    """Parse the third prognostic line of the CLASS block.

    Parameters
    ----------
    prog_line : str
        Prognostic line containing numeric parameters.

    Returns
    -------
    dict[str, float]
        Parsed prognostic parameters.
    """
    # remove comments
    prog_line = remove_comments(prog_line)

    # strip and split based on whitespace
    prog_line = prog_line.strip().split()

    # please note that the parameters are hard-coded and match the inputs
    # of MESHFlow's `meshflow.utility.render_class_template` function.
    prog_params = {
        'rcan': float(prog_line[0]),
        'scan': float(prog_line[1]),
        'sno': float(prog_line[2]),
        'albs': float(prog_line[3]),
        'rhos': float(prog_line[4]),
        'gro': float(prog_line[5])
    }

    return prog_params

def iter_sections(
    text: str,
    drop_separators: bool=True,
):
    """Iterate over named sections in a hydrology/ini-like file.

    Parameters
    ----------
    text : str
        Full text of the configuration file.
    drop_separators : bool, default ``True``
        If ``True``, remove separator lines when constructing section bodies.

    Yields
    ------
    tuple[str, str]
        Section header and body text.
    """
    # default re directives
    HEADER_RE = re.compile(r'^#{3,}\s*(.*?)\s*#*\s*$', re.MULTILINE)
    SEP_LINE_RE = re.compile(r'^-{3,}#.*$')

    # defining matching headers
    matches = list(HEADER_RE.finditer(text))
    def not_sep(line):
        return not (drop_separators and SEP_LINE_RE.match(line))

    if not matches:
        body = "\n".join(l for l in text.splitlines() if not_sep(l)).strip()
        if body:
            yield ("Preamble", body)
        return

    # preamble
    first_start = matches[0].start()
    if first_start > 0:
        pre_lines = [l for l in text[:first_start].splitlines() if not_sep(l)]
        pre = "\n".join(pre_lines).strip()
        if pre:
            yield ("Preamble", pre)

    # extracting sections
    for i, m in enumerate(matches):
        header = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i+1].start() if i+1 < len(matches) else len(text)
        block = text[body_start:body_end]
        lines = [l for l in block.splitlines() if not_sep(l)]
        body = "\n".join(lines).strip('\n')
        yield (header, body)

def hydrology_section_divide(
    hydrology_file: os.PathLike | str,
) -> List[str]:
    """Split a hydrology file into content sections.

    Parameters
    ----------
    hydrology_file : path-like
        File path to the MESH hydrology configuration.

    Returns
    -------
    list[str]
        List of section bodies, in file order.
    """
    text = Path(hydrology_file).read_text(encoding="utf-8")
    sections = [b for h, b in iter_sections(text)]

    return sections

def param_name_gen(
    computational_unit: NameType,
    name: NameType,
) -> str:
    """Generate a canonical parameter name for templating.

    Parameters
    ----------
    computational_unit : str or int or float
        Identifier of the hydrological unit (e.g., GRU index).
    name : str or int or float
        Base parameter name.

    Returns
    -------
    str
        Uppercased name prefixed with ``_`` and the unit (e.g., ``_1FOO``).
    """
    # making strings
    _unit = str(computational_unit)
    _name = str(name)

    # A naming template like the following can be
    # generalized to all models: _+`_unit`+`_name`
    param_name = '_' + _unit.upper() + _name.upper()
    
    return param_name

def replace_prefix_in_last_two_lines(
    path: PathLike,
    replacements: Tuple[str],
    width: int = 17):
    """Overwrite a fixed-width prefix on the last two lines of a file.

    Parameters
    ----------
    path : path-like
        File to modify.
    replacements : tuple[str]
        Two replacement strings applied to the last two lines respectively.
    width : int, default 17
        Number of prefix characters to overwrite.

    Raises
    ------
    ValueError
        If the file contains fewer than two lines.
    """
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    if len(lines) < 2:
        raise ValueError("File has fewer than two lines.")

    # Prepare replacement to exactly `width` characters
    for idx, r in enumerate(replacements):
        rep = (r[:width]).ljust(width)

        line = lines[idx-2]
        # Preserve newline if present
        newline = "\n" if line.endswith("\n") else ""
        body = line[:-1] if newline else line
        lines[idx-2] = rep + body[width:] + newline

    p.write_text("".join(lines), encoding="utf-8")

    return

def spaces(h: int) -> str:
    """Return padding spaces based on hour value.

    Parameters
    ----------
    h : int
        Hour component (0-23).

    Returns
    -------
    str or None
        ``"   "`` for ``h < 10``; ``"  "`` for ``10 < h < 25``;
        otherwise ``None``.
    """
    # if h is only one digit, return 3 spaces
    if h < 10:
        return " " * 3
    elif 10 < h < 25:
        return " " * 2
    
    return