API Reference
=============

.. currentmodule:: fiatmodel

Core
----

.. autosummary::
	:toctree: generated
	:nosignatures:

	core.Calibration

Utilities
---------

.. autosummary::
	:toctree: generated
	:nosignatures:

	utils.union_sorted_times

Calibration
-----------

.. autosummary::
	:toctree: generated
	:nosignatures:

	calibration.optimizer.OptimizerTemplateEngine
	calibration.ostrich.templating.OstrichTemplateEngine

Models
------

.. autosummary::
	:toctree: generated
	:nosignatures:

	models.builder.ModelBuilder
	models.mesh.model.MESH
	models.mesh.funcs.remove_comments
	models.mesh.funcs.class_section_divide
	models.mesh.funcs.parse_class_meta_data
	models.mesh.funcs.determine_gru_type
	models.mesh.funcs.parse_class_veg1
	models.mesh.funcs.parse_class_veg2
	models.mesh.funcs.parse_class_hyd1
	models.mesh.funcs.parse_class_hyd2
	models.mesh.funcs.parse_class_soil
	models.mesh.funcs.parse_class_prog1
	models.mesh.funcs.parse_class_prog2
	models.mesh.funcs.parse_class_prog3
	models.mesh.funcs.iter_sections
	models.mesh.funcs.hydrology_section_divide
	models.mesh.funcs.param_name_gen
	models.mesh.funcs.replace_prefix_in_last_two_lines
	models.mesh.funcs.spaces

Evaluation Script
-----------------

These are utilities used during calibration-time evaluation.

.. autosummary::
	:toctree: generated
	:nosignatures:

	models.mesh.eval

