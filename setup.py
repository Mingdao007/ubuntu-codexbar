from setuptools import find_packages, setup

setup(
    name="codexbar",
    version="0.1.0",
    description="Saved-profile switcher and usage inspector for Codex on Linux and macOS",
    package_dir={"": "src"},
    packages=find_packages("src"),
    python_requires=">=3.10",
    entry_points={
        "console_scripts": [
            "codexbar=codexbar.cli:main",
        ]
    },
)
