from setuptools import setup, find_packages

setup(
    name="scalpellab",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "streamlit",
        "pandas",
        "PyMuPDF",
        "pillow",
        "openpyxl",
    ],
    entry_points={
        "console_scripts": [
            "scalpel=scalpellab.cli.main:main",
        ],
    },
    python_requires=">=3.7",
)
