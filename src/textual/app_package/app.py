
class App():

    title: Reactive[str] = Reactive("", compute=False)
    sub_title: Reactive[str] = Reactive("", compute=False)
    
    def __init__(self):
        pass

    def validate_title(self, title: Any) -> str:
        """Make sure the title is set to a string."""
        return str(title)

    def validate_sub_title(self, sub_title: Any) -> str:
        """Make sure the sub-title is set to a string."""
        return str(sub_title)
    
    # add all left methods