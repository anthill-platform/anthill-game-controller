
from setuptools import setup, find_packages

DEPENDENCIES = [
    "anthill-common"
]

setup(
    name='anthill-game-controller',
    setup_requires=["pypigit-version"],
    git_version="0.1.0",
    description='Game servers hosting & matchmaking service for Anthill platform Edit Add topics',
    author='desertkun',
    license='MIT',
    author_email='desertkun@gmail.com',
    url='https://github.com/anthill-platform/anthill-game-controller',
    namespace_packages=["anthill"],
    packages=find_packages(),
    zip_safe=False,
    install_requires=DEPENDENCIES
)
