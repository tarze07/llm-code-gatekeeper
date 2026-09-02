using Xunit;
using Moq;

namespace Demo.Tests;

public class MockEchoTests
{
    [Fact]
    public void Echo_ComparesMockToItself()
    {
        var mock = new Mock<IFoo>();
        mock.Setup(x => x.Bar()).Returns(5);
        Assert.Equal(5, mock.Object.Bar());
    }
}
