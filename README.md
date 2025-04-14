# Project README

This project processes climate and emissions data for seven South Asian countries using ERA5 and EDGAR datasets, with server-client scripts for data handling and a ClimateGPT API for generating humanized responses.

## Data Sources and Preprocessing

1. **ERA5 Monthly Means Data**
   - **Source**: Download from [Copernicus Climate Data Store](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels-monthly-means?tab=download).
   - **Preprocessing**: Preprocess the data for seven South Asian countries (not specified here) to be used in:
     - `era5mcp.py` (server)
     - `era5optim.py` (client)

2. **EDGAR Emissions Data**
   - **Source**: Obtain from [EDGAR GHG 2024 Dataset](https://edgar.jrc.ec.europa.eu/dataset_ghg2024).
   - **Usage**: Processed for emissions calculations in:
     - `emissions_mcp.py` (server)
     - `EDGARclient.py` (client)

## API Integration for Humanized Responses

The project uses the ClimateGPT model to generate humanized responses:
- **API Endpoint**: `https://erasmus.ai/models/climategpt_8b_latest/v1/chat/completions`
- **Headers**: `{"Content-Type": "application/json"}`
- **Authentication**:
  ```python
  auth = (os.getenv("API_USER"), os.getenv("API_KEY"))
  Ensure the API_USER and API_KEY environment variables are set to correspond with the credentials stored in your auth.enc file.
