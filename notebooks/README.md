# Jupyter Notebook Tutorials

This directory contains source Jupyter notebook tutorials demonstrating linkml-term-validator usage.

## Notebooks

**CLI-Focused Tutorials (Recommended Starting Point):**

1. **[01_getting_started.ipynb](01_getting_started.ipynb)** - CLI Basics with Success & Failure Examples
   - Uses `%%bash` cells for authentic CLI experience
   - Schema validation (valid and invalid cases)
   - Dynamic enum validation
   - Common CLI options (--verbose, --strict, --cache-dir)
   - Learning from validation failures

2. **[02_advanced_usage.ipynb](02_advanced_usage.ipynb)** - Advanced CLI Features & Troubleshooting
   - Working with local OBO files (offline validation)
   - Custom OAK configurations (oak_config.yaml)
   - Binding validation with nested objects
   - Advanced CLI flags (--labels, --no-dynamic-enums, --no-bindings)
   - Troubleshooting common errors

**Python API (For Programmatic Usage):**

3. **[03_python_api.ipynb](03_python_api.ipynb)** - Python Integration
   - Using ValidationPlugin classes
   - Combining multiple plugins
   - Error handling and reporting
   - Integration into data processing pipelines

## Workflow

**Source notebooks** (this directory) → **Execute with papermill** → **Render to HTML** → **Display in docs**

```
notebooks/                     # Source (git-tracked)
  ├── 01_getting_started.ipynb
  └── 02_advanced_usage.ipynb

notebooks/output/              # Executed notebooks (git-ignored)
  ├── 01_getting_started.ipynb
  └── 02_advanced_usage.ipynb

docs/notebooks/                # Rendered HTML (git-ignored)
  ├── 01_getting_started.html
  └── 02_advanced_usage.html
```

## Commands

### Render for Documentation

Execute notebooks and convert to HTML for mkdocs:

```bash
just render-notebooks
```

This runs papermill (to execute) then nbconvert (to generate HTML). The HTML files are displayed in the documentation.

### Interactive Development

Start Jupyter Lab for interactive development:

```bash
just jupyter
```

### Testing Only

Run all notebooks with papermill to test they execute without errors:

```bash
just run-notebooks
```

Run a specific notebook:

```bash
just run-notebook 01_getting_started.ipynb
```

### Clean Outputs

Remove all generated outputs:

```bash
just clean-notebooks
```

## Why This Approach?

We use **pre-rendered HTML** instead of mkdocs plugins because:

1. ✅ **Separation of concerns**: Notebook execution (can fail) is separate from docs build (should always work)
2. ✅ **Fast mkdocs builds**: Pre-rendered, mkdocs just copies HTML
3. ✅ **CI-friendly**: Can fail on notebook execution errors separately
4. ✅ **Full Jupyter styling**: nbconvert provides complete Jupyter Lab CSS
5. ✅ **No plugin limitations**: HTML works naturally in mkdocs

## Running Locally

### Prerequisites

Make sure you have linkml-term-validator installed:

```bash
pip install linkml-term-validator
# or
uv pip install linkml-term-validator
```

### Using Jupyter

```bash
# Install jupyter
pip install jupyter

# Navigate to the notebooks directory
cd notebooks

# Start Jupyter
jupyter notebook
```

### Using JupyterLab

```bash
# Install jupyterlab
pip install jupyterlab

# Start JupyterLab
jupyter lab
```

### Using VS Code

VS Code has excellent Jupyter support:
1. Install the Jupyter extension
2. Open a `.ipynb` file
3. Select your Python kernel
4. Run cells interactively

## Notes

- The notebooks use temporary directories and clean up after themselves
- OAK adapter downloads may take time on first run (subsequent runs use cache)
- For offline use, see the "Working with Local OBO Files" section in the advanced tutorial
