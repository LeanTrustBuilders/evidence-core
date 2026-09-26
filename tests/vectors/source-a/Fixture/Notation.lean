import Fixture.Basic

namespace Fixture

/-- A notation: its parser is a node, and what it expands to (`double`) is among its dependencies. -/
notation "𝟚" n:max => double n

/-- Stated with the notation. -/
theorem double_two : 𝟚 2 = 4 := rfl

end Fixture
