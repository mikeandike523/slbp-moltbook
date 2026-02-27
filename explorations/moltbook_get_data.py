import click

from tools.moltbook.get_data import execute as moltbook_get_data

session_data={}




@click.command()
@click.argument("path", type=str, required=True, nargs=1)
def main(path):
    print(
        moltbook_get_data({
            "path":path
        },{})
    )

if __name__ == "__main__":
    main()