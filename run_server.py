import os
import sys
import uvicorn

if __name__ == '__main__':
    # Change to the sign-system directory
    os.chdir(os.path.join(os.path.dirname(__file__), 'sign-system'))

    # Add current directory to Python path
    sys.path.insert(0, '.')

    # Run uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_excludes=["*.json", "*.pdf", "*.png", "*.ttf"],
    )
