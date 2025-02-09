import dash
import dash_core_components as dcc
import dash_html_components as html
import dash_table as dt

from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate

import os
import logging

from flask import make_response, jsonify, request
from flask_caching import Cache

from crystal_toolkit.core.mpcomponent import MPComponent
from crystal_toolkit.helpers.layouts import *
from crystal_toolkit.helpers.mprester import MPRester
from crystal_toolkit import __file__ as module_path

import crystal_toolkit.components as ctc

from pymatgen import Structure, Molecule
from pymatgen.analysis.graphs import StructureGraph, MoleculeGraph
from pymatgen import __version__ as pmg_version

from json import loads
from uuid import uuid4
from urllib import parse
from random import choice
from ast import literal_eval
from monty.serialization import loadfn

# choose a default structure on load
path = os.path.join(os.path.dirname(module_path), "apps/assets/task_ids_on_load.json")
DEFAULT_MPIDS = loadfn(path)

################################################################################
# region SET UP APP
################################################################################

meta_tags = [  # TODO: add og-image, etc., title
    {
        "name": "description",
        "content": "Crystal Toolkit allows you to import, view, analyze and transform "
        "crystal structures and molecules using the full power of the Materials "
        "Project.",
    }
]

DEBUG_MODE = literal_eval(os.environ.get("CRYSTAL_TOOLKIT_DEBUG_MODE", "False").title())
MP_EMBED_MODE = literal_eval(
    os.environ.get("CRYSTAL_TOOLKIT_MP_EMBED_MODE", "False").title()
)

assets_folder = os.path.join(os.path.dirname(module_path), "apps/assets/")
crystal_toolkit_app = dash.Dash(
    __name__, meta_tags=meta_tags, assets_folder=assets_folder
)
crystal_toolkit_app.config["suppress_callback_exceptions"] = True
crystal_toolkit_app.title = "Crystal Toolkit"
crystal_toolkit_app.scripts.config.serve_locally = True

if not MP_EMBED_MODE:
    crystal_toolkit_app.config["assets_ignore"] = r".*\.mpembed\..*"
    box_size = "65vmin"
else:
    # reduce zoom level and box size due to iframe on materialsproject.org
    ctc.StructureMoleculeComponent.default_scene_settings["defaultZoom"] = 0.5
    box_size = "50vmin"


crystal_toolkit_app.server.secret_key = str(uuid4())
server = crystal_toolkit_app.server


# endregion
##########


################################################################################
# region SET UP CACHE
################################################################################

if os.environ.get("REDIS_URL", ""):
    cache = Cache(
        crystal_toolkit_app.server,
        config={
            "CACHE_TYPE": "redis",
            "CACHE_REDIS_URL": os.environ.get("REDIS_URL", ""),
        },
    )
elif DEBUG_MODE:
    # disable cache in debug
    cache = Cache(crystal_toolkit_app.server, config={"CACHE_TYPE": "null"})
else:
    crystal_toolkit_app.logger.error(
        "Failed to connect to Redis cache, falling back to file system cache."
    )
    cache = Cache(crystal_toolkit_app.server, config={"CACHE_TYPE": "simple"})

# endregion


################################################################################
# region SET UP LOGGING
################################################################################

logger = logging.getLogger(crystal_toolkit_app.title)

# endregion


################################################################################
# region INSTANTIATE CORE COMPONENTS
################################################################################

ctc.register_app(crystal_toolkit_app)
ctc.register_cache(cache)

supercell = ctc.SupercellTransformationComponent()
grain_boundary = ctc.GrainBoundaryTransformationComponent()
oxi_state = ctc.AutoOxiStateDecorationTransformationComponent()
slab = ctc.SlabTransformationComponent()
substitution = ctc.SubstitutionTransformationComponent()

transformation_component = ctc.AllTransformationsComponent(
    transformations=[supercell, slab, grain_boundary, oxi_state, substitution]
)

struct_component = ctc.StructureMoleculeComponent()
struct_component.attach_from(transformation_component, origin_store_name="out")

# TODO: change to link to struct_or_mol ?
download_component = ctc.DownloadPanelComponent(origin_component=struct_component)

search_component = ctc.SearchComponent()
upload_component = ctc.StructureMoleculeUploadComponent()

robocrys_component = ctc.RobocrysComponent(origin_component=struct_component)
magnetism_component = ctc.MagnetismComponent(origin_component=struct_component)
xrd_component = ctc.XRayDiffractionPanelComponent(origin_component=struct_component)
pbx_component = ctc.PourbaixDiagramPanelComponent(origin_component=struct_component)

symmetry_component = ctc.SymmetryComponent(origin_component=struct_component)
localenv_component = ctc.LocalEnvironmentPanel()
localenv_component.attach_from(
    origin_component=struct_component, origin_store_name="graph"
)

bonding_graph_component = ctc.BondingGraphComponent()
bonding_graph_component.attach_from(struct_component, origin_store_name="graph")
# link bonding graph color scheme to parent color scheme
bonding_graph_component.attach_from(
    struct_component,
    this_store_name="display_options",
    origin_store_name="display_options",
)

# favorites_component = ctc.FavoritesComponent()
# favorites_component.attach_from(search_component, this_store_name="current-mpid")

if MP_EMBED_MODE:
    submit_snl_panel = ctc.SubmitSNLPanel(origin_component=struct_component)
    action_div = html.Div(
        [submit_snl_panel.panel_layout, download_component.panel_layout]
    )
else:
    action_div = html.Div([download_component.panel_layout])

panels = [
    symmetry_component,
    bonding_graph_component,
    localenv_component,
    xrd_component,
    robocrys_component,
]

if MP_EMBED_MODE:
    mp_section = (html.Div(),)
else:

    bsdos_component = ctc.BandstructureAndDosPanelComponent(
        origin_component=search_component
    )
    # grain_boundary_panel = ctc.GrainBoundaryPanel(origin_component=search_component)
    xas_component = ctc.XASPanelComponent(origin_component=search_component)
    pd_component = ctc.PhaseDiagramPanelComponent(origin_component=struct_component)
    literature_component = ctc.LiteratureComponent(origin_component=struct_component)

    mp_panels = [
        pd_component,
        pbx_component,
        magnetism_component,
        xas_component,
        bsdos_component,
        # grain_boundary_panel,
        literature_component,
    ]

    mp_section = (
        H3("Materials Project"),
        html.Div([panel.panel_layout for panel in mp_panels], id="mp_panels"),
    )


body_layout = [
    html.Br(),
    H3("Transform"),
    html.Div([transformation_component.standard_layout]),
    html.Br(),
    H3("Analyze"),
    html.Div([panel.panel_layout for panel in panels], id="panels"),
    html.Br(),
    *mp_section,
]

STRUCT_VIEWER_SOURCE = transformation_component.id()


banner = html.Div(id="banner")
if DEBUG_MODE:
    banner = html.Div(
        [
            html.Br(),
            MessageContainer(
                [
                    MessageHeader("Warning"),
                    MessageBody(
                        dcc.Markdown(
                            "This is a pre-release version of Crystal Toolkit and "
                            "may not behave reliably. Please visit "
                            "[https://viewer.materialsproject.org](https://viewer.materialsproject.org) "
                            "for a stable version."
                        )
                    ),
                ],
                kind="warning",
            ),
        ],
        id="banner",
    )

api_offline, api_error = True, "Unknown error connecting to Materials Project API."
try:
    with MPRester() as mpr:
        api_check = mpr._make_request("/api_check")
    if not api_check.get("api_key_valid", False):
        api_error = (
            "Materials Project API key not supplied or not valid, "
            "please set PMG_MAPI_KEY in your environment."
        )
    else:
        api_offline = False
except Exception as exception:
    api_error = str(exception)
if api_offline:
    banner = html.Div(
        [
            html.Br(),
            MessageContainer(
                [
                    MessageHeader("Error: Cannot connect to Materials Project"),
                    MessageBody(api_error),
                ],
                kind="danger",
            ),
        ],
        id="banner",
    )


# endregion


################################################################################
# region CREATE OTHER LAYOUT ELEMENTS
################################################################################


footer = ctc.Footer(
    html.Div(
        [
            # html.Iframe(
            #    src="https://ghbtns.com/github-btn.html?user=materialsproject&repo=crystaltoolkit&type=star&count=true",
            #    style={
            #        "frameborder": False,
            #        "scrolling": False,#
            #        "width": "72px",
            #        "height": "20px",
            #    },
            # ),
            # html.Br(), Button([Icon(kind="cog", fill="r"), html.Span("Customize")], kind="light", size='small'),
            dcc.Markdown(
                f"App created by [Crystal Toolkit Development Team](https://github.com/materialsproject/crystaltoolkit/graphs/contributors).  \n"
                f"Bug reports and feature requests gratefully accepted, please send them to [@mkhorton](mailto:mkhorton@lbl.gov).  \n"
                f"Powered by [The Materials Project](https://materialsproject.org), "
                f"[pymatgen v{pmg_version}](http://pymatgen.org) and "
                f"[Dash by Plotly](https://plot.ly/products/dash/). "
                f"Deployed on [Spin](http://www.nersc.gov/users/data-analytics/spin/)."
            )
        ],
        className="content has-text-centered",
    ),
    style={"padding": "1rem 1rem 1rem", "background-color": "inherit"},
)

panel_choices = dcc.Dropdown(
    options=[{"label": panel.title, "value": idx} for idx, panel in enumerate(panels)],
    multi=True,
    value=[idx for idx in range(len(panels))],
    id="panel-choices",
)

panel_description = dcc.Markdown(
    [
        "Crystal Toolkit offers various *panels* which each provide different ways "
        "of analyzing, transforming or retrieving information about a material using "
        "resources and tools available to The Materials Project. Some panels "
        "retrieve data or run algorithms on demand, so please allow some time "
        "for them to run. Explore these panels below."
    ],
    className="mpc-panel-description",
)


# endregion


################################################################################
# region  DEFINE MAIN LAYOUT
################################################################################

master_layout = Container(
    [
        dcc.Location(id="url", refresh=False),
        MPComponent.all_app_stores(),
        # dcc.Store(storage_type="session", id="session_store"),
        banner,
        Section(
            [
                Columns(
                    [
                        Column(
                            [
                                struct_component.title_layout,
                                html.Div(
                                    # [
                                    #    html.A(
                                    #        "Documentation",
                                    #        href="https://docs.crystaltoolkit.org",
                                    #    )
                                    # ],
                                    # [favorites_component.button_layout],
                                    style={"float": "right"}
                                ),
                            ]
                        )
                    ]
                ),
                Columns(
                    [
                        Column(
                            [
                                # TODO: test responsiveness of layout on phone
                                Box(
                                    struct_component.struct_layout,
                                    style={
                                        "width": box_size,
                                        "height": box_size,
                                        "min-width": "300px",
                                        "min-height": "300px",
                                        "max-width": "600px",
                                        "max-height": "600px",
                                        "overflow": "hidden",
                                        "padding": "0.25rem",
                                        "margin-bottom": "0.5rem",
                                    },
                                ),
                                html.Div(
                                    [
                                        html.Div(
                                            struct_component.legend_layout,
                                            style={"float": "left"},
                                        ),
                                        html.Div(
                                            [struct_component.screenshot_layout],
                                            style={"float": "right"},
                                        ),
                                    ],
                                    style={
                                        "width": box_size,
                                        "min-width": "300px",
                                        "margin-bottom": "40px",
                                    },
                                ),
                            ],
                            narrow=True,
                        ),
                        Column(
                            [
                                Reveal(
                                    [
                                        search_component.standard_layout,
                                        upload_component.standard_layout,
                                        # favorites_component.favorite_materials_layout,
                                    ],
                                    title="Load Crystal or Molecule",
                                    open=True,
                                    style={"line-height": "1"},
                                    id="load",
                                ),
                                Reveal(
                                    [struct_component.options_layout],
                                    title="Display Options",
                                    id="display-options",
                                ),
                                action_div,
                                # favorites_component.notes_layout,
                            ],
                            style={"width": box_size, "max-width": box_size},
                        ),
                    ],
                    desktop_only=False,
                    centered=False,
                ),
                Columns([Column(body_layout)]),
            ]
        ),
        Section(footer),
    ]
)

crystal_toolkit_app.layout = master_layout

# endregion


################################################################################
# region SET UP APP-SPECIFIC CALLBACKS
################################################################################


@crystal_toolkit_app.callback(
    Output(search_component.id("input"), "value"), [Input("url", "href")]
)
def update_search_term_on_page_load(href):
    if href is None:
        raise PreventUpdate
    pathname = str(parse.urlparse(href).path).split("/")
    if len(pathname) <= 1:
        raise PreventUpdate
    elif not pathname[1]:
        return choice(DEFAULT_MPIDS)
    else:
        return pathname[1].replace("+", " ")


@crystal_toolkit_app.callback(
    Output(search_component.id("input"), "n_submit"),
    [Input(search_component.id("input"), "value")],
    [State(search_component.id("input"), "n_submit")],
)
def perform_search_on_page_load(search_term, n_submit):
    # TODO: when multiple output callbacks are supported, should also update n_submit_timestamp
    if n_submit is None:
        return 1
    else:
        raise PreventUpdate


@crystal_toolkit_app.callback(
    Output("url", "pathname"), [Input(search_component.id(), "data")]
)
def update_url_pathname_from_search_term(data):
    if data is None or "mpid" not in data:
        raise PreventUpdate
    return data["mpid"]


@crystal_toolkit_app.callback(
    Output(STRUCT_VIEWER_SOURCE, "data"),
    [Input(search_component.id(), "data"), Input(upload_component.id(), "data")],
)
def master_update_structure(search_mpid, upload_data):

    if not search_mpid and not upload_data:
        raise PreventUpdate

    search_mpid = search_mpid or {}
    upload_data = upload_data or {}

    time_searched = search_mpid.get("time_requested", -1)
    time_uploaded = upload_data.get("time_requested", -1)

    if time_searched > time_uploaded:

        if search_mpid is None or "mpid" not in search_mpid:
            raise PreventUpdate

        with MPRester() as mpr:
            try:
                struct = mpr.get_task_data(search_mpid["mpid"], "structure")[0][
                    "structure"
                ]
                print("Struct from task.")
            except:
                struct = mpr.get_structure_by_material_id(search_mpid["mpid"])
                print("Struct from material.")
    else:

        struct = MPComponent.from_data(upload_data["data"])

    return MPComponent.to_data(struct.as_dict())


# @crystal_toolkit_app.callback(
#    Output(struct_component.id(""), ""),
#    [Input(transformation_component.id(""), "")],
#    [State(struct_component.id(""), "")]
# )
# def change_input_structure(transformation, current_state):
# if transformation active and current state != input
#


# endregion

################################################################################
# Run server :-)
################################################################################


if __name__ == "__main__":
    crystal_toolkit_app.run_server(debug=DEBUG_MODE, port=8050)
