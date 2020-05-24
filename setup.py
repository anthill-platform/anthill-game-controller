
from setuptools import setup, find_namespace_packages

DEPENDENCIES = [
    "anthill-common>=0.2.4"
]

setup(
    name='anthill-game-controller',
    package_data={
      "anthill.game.controller": ["anthill/game/controller/sql", "anthill/game/controller/static"]
    },
    version='0.2.6',
    description='Game servers hosting & matchmaking service for Anthill platform Edit Add topics',
    author='desertkun',
    license='MIT',
    author_email='desertkun@gmail.com',
    url='https://github.com/anthill-platform/anthill-game-controller',
    namespace_packages=["anthill"],
    include_package_data=True,
    packages=find_namespace_packages(include=["anthill.*"]),
    zip_safe=False,
    install_requires=DEPENDENCIES
)
