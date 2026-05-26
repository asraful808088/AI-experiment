from inference_sdk import InferenceHTTPClient
import json

# Connect to your workflow
client = InferenceHTTPClient(
    api_url="https://serverless.roboflow.com",
    api_key="e0XrTuBWNdGUfD1amNao"
)

# Run your workflow
result = client.run_workflow(
    workspace_name="valo-s-workspace",
    workflow_id="find-damage-part-of-cars",
    images={"image": "0376.JPEG"},
    use_cache=True
)

# Create HTML with annotations
if result and len(result) > 0:
    predictions = result[0].get('predictions', {}).get('predictions', [])
    visualization = result[0].get('visualization', '')
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Damage Detection Results</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .container {{ display: flex; flex-wrap: wrap; gap: 20px; }}
            .image-container {{ position: relative; display: inline-block; }}
            .result-image {{ max-width: 100%; height: auto; }}
            .predictions {{
                background: #f5f5f5;
                padding: 15px;
                border-radius: 8px;
                max-width: 400px;
            }}
            .prediction-item {{
                margin: 10px 0;
                padding: 10px;
                background: white;
                border-radius: 4px;
                border-left: 4px solid #ff4444;
            }}
            .confidence {{
                color: #28a745;
                font-weight: bold;
            }}
        </style>
    </head>
    <body>
        <h1>Car Damage Detection Results</h1>
        <div class="container">
            <div class="image-container">
                <img class="result-image" src="data:image/jpeg;base64,{visualization}" />
            </div>
            <div class="predictions">
                <h2>Detected Damages ({len(predictions)})</h2>
    """
    
    for pred in predictions:
        confidence = pred.get('confidence', 0) * 100
        width = pred.get('width', 0)
        height = pred.get('height', 0)
        
        html_content += f"""
                <div class="prediction-item">
                    <strong>Damage detected</strong><br>
                    Confidence: <span class="confidence">{confidence:.1f}%</span><br>
                    Size: {width:.0f} x {height:.0f} pixels
                </div>
        """
    
    html_content += """
            </div>
        </div>
    </body>
    </html>
    """
    
    # Save to file
    with open('detection_results.html', 'w') as f:
        f.write(html_content)
    
    print("HTML file saved as detection_results.html")
    
    # If in Jupyter notebook, display directly
    try:
        from IPython.display import display, HTML
        display(HTML(html_content))
    except:
        pass