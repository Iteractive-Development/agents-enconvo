# Detail reference

## Data Access Patterns

### Entity Framework Core

```csharp
// DbContext configuration
public class AppDbContext : DbContext
{
    public DbSet<Product> Products => Set<Product>();
    public DbSet<Order> Orders => Set<Order>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        // Apply all configurations from assembly
        modelBuilder.ApplyConfigurationsFromAssembly(typeof(AppDbContext).Assembly);

        // Global query filters
        modelBuilder.Entity<Product>().HasQueryFilter(p => !p.IsDeleted);
    }
}

// Entity configuration
public class ProductConfiguration : IEntityTypeConfiguration<Product>
{
    public void Configure(EntityTypeBuilder<Product> builder)
    {
        builder.ToTable("Products");

        builder.HasKey(p => p.Id);
        builder.Property(p => p.Id).HasMaxLength(40);
        builder.Property(p => p.Name).HasMaxLength(200).IsRequired();
        builder.Property(p => p.Price).HasPrecision(18, 2);

        builder.HasIndex(p => p.Sku).IsUnique();
        builder.HasIndex(p => new { p.CategoryId, p.Name });

        builder.HasMany(p => p.OrderItems)
            .WithOne(oi => oi.Product)
            .HasForeignKey(oi => oi.ProductId);
    }
}

// Repository with EF Core
public class ProductRepository : IProductRepository
{
    private readonly AppDbContext _context;

    public async Task<Product?> GetByIdAsync(string id, CancellationToken ct = default)
    {
        return await _context.Products
            .AsNoTracking()
            .FirstOrDefaultAsync(p => p.Id == id, ct);
    }

    public async Task<IReadOnlyList<Product>> SearchAsync(
        ProductSearchCriteria criteria,
        CancellationToken ct = default)
    {
        var query = _context.Products.AsNoTracking();

        if (!string.IsNullOrWhiteSpace(criteria.SearchTerm))
            query = query.Where(p => EF.Functions.Like(p.Name, $"%{criteria.SearchTerm}%"));

        if (criteria.CategoryId.HasValue)
            query = query.Where(p => p.CategoryId == criteria.CategoryId);

        if (criteria.MinPrice.HasValue)
            query = query.Where(p => p.Price >= criteria.MinPrice);

        if (criteria.MaxPrice.HasValue)
            query = query.Where(p => p.Price <= criteria.MaxPrice);

        return await query
            .OrderBy(p => p.Name)
            .Skip((criteria.Page - 1) * criteria.PageSize)
            .Take(criteria.PageSize)
            .ToListAsync(ct);
    }
}
```

### Dapper for Performance

```csharp
public class DapperProductRepository : IProductRepository
{
    private readonly IDbConnection _connection;

    public async Task<Product?> GetByIdAsync(string id, CancellationToken ct = default)
    {
        const string sql = """
            SELECT Id, Name, Sku, Price, CategoryId, Stock, CreatedAt
            FROM Products
            WHERE Id = @Id AND IsDeleted = 0
            """;

        return await _connection.QueryFirstOrDefaultAsync<Product>(
            new CommandDefinition(sql, new { Id = id }, cancellationToken: ct));
    }

    public async Task<IReadOnlyList<Product>> SearchAsync(
        ProductSearchCriteria criteria,
        CancellationToken ct = default)
    {
        var sql = new StringBuilder("""
            SELECT Id, Name, Sku, Price, CategoryId, Stock, CreatedAt
            FROM Products
            WHERE IsDeleted = 0
            """);

        var parameters = new DynamicParameters();

        if (!string.IsNullOrWhiteSpace(criteria.SearchTerm))
        {
            sql.Append(" AND Name LIKE @SearchTerm");
            parameters.Add("SearchTerm", $"%{criteria.SearchTerm}%");
        }

        if (criteria.CategoryId.HasValue)
        {
            sql.Append(" AND CategoryId = @CategoryId");
            parameters.Add("CategoryId", criteria.CategoryId);
        }

        if (criteria.MinPrice.HasValue)
        {
            sql.Append(" AND Price >= @MinPrice");
            parameters.Add("MinPrice", criteria.MinPrice);
        }

        if (criteria.MaxPrice.HasValue)
        {
            sql.Append(" AND Price <= @MaxPrice");
            parameters.Add("MaxPrice", criteria.MaxPrice);
        }

        sql.Append(" ORDER BY Name OFFSET @Offset ROWS FETCH NEXT @PageSize ROWS ONLY");
        parameters.Add("Offset", (criteria.Page - 1) * criteria.PageSize);
        parameters.Add("PageSize", criteria.PageSize);

        var results = await _connection.QueryAsync<Product>(
            new CommandDefinition(sql.ToString(), parameters, cancellationToken: ct));

        return results.ToList();
    }

    // Multi-mapping for related data
    public async Task<Order?> GetOrderWithItemsAsync(int orderId, CancellationToken ct = default)
    {
        const string sql = """
            SELECT o.*, oi.*, p.*
            FROM Orders o
            LEFT JOIN OrderItems oi ON o.Id = oi.OrderId
            LEFT JOIN Products p ON oi.ProductId = p.Id
            WHERE o.Id = @OrderId
            """;

        var orderDictionary = new Dictionary<int, Order>();

        await _connection.QueryAsync<Order, OrderItem, Product, Order>(
            new CommandDefinition(sql, new { OrderId = orderId }, cancellationToken: ct),
            (order, item, product) =>
            {
                if (!orderDictionary.TryGetValue(order.Id, out var existingOrder))
                {
                    existingOrder = order;
                    existingOrder.Items = new List<OrderItem>();
                    orderDictionary.Add(order.Id, existingOrder);
                }

                if (item != null)
                {
                    item.Product = product;
                    existingOrder.Items.Add(item);
                }

                return existingOrder;
            },
            splitOn: "Id,Id");

        return orderDictionary.Values.FirstOrDefault();
    }
}
```

## Caching Patterns

### Multi-Level Cache with Redis

```csharp
public class CachedProductService : IProductService
{
    private readonly IProductRepository _repository;
    private readonly IMemoryCache _memoryCache;
    private readonly IDistributedCache _distributedCache;
    private readonly ILogger<CachedProductService> _logger;

    private static readonly TimeSpan MemoryCacheDuration = TimeSpan.FromMinutes(1);
    private static readonly TimeSpan DistributedCacheDuration = TimeSpan.FromMinutes(15);

    public async Task<Product?> GetByIdAsync(string id, CancellationToken ct = default)
    {
        var cacheKey = $"product:{id}";

        // L1: Memory cache (in-process, fastest)
        if (_memoryCache.TryGetValue(cacheKey, out Product? cached))
        {
            _logger.LogDebug("L1 cache hit for {CacheKey}", cacheKey);
            return cached;
        }

        // L2: Distributed cache (Redis)
        var distributed = await _distributedCache.GetStringAsync(cacheKey, ct);
        if (distributed != null)
        {
            _logger.LogDebug("L2 cache hit for {CacheKey}", cacheKey);
            var product = JsonSerializer.Deserialize<Product>(distributed);

            // Populate L1
            _memoryCache.Set(cacheKey, product, MemoryCacheDuration);
            return product;
        }

        // L3: Database
        _logger.LogDebug("Cache miss for {CacheKey}, fetching from database", cacheKey);
        var fromDb = await _repository.GetByIdAsync(id, ct);

        if (fromDb != null)
        {
            var serialized = JsonSerializer.Serialize(fromDb);

            // Populate both caches
            await _distributedCache.SetStringAsync(
                cacheKey,
                serialized,
                new DistributedCacheEntryOptions
                {
                    AbsoluteExpirationRelativeToNow = DistributedCacheDuration
                },
                ct);

            _memoryCache.Set(cacheKey, fromDb, MemoryCacheDuration);
        }

        return fromDb;
    }

    public async Task InvalidateAsync(string id, CancellationToken ct = default)
    {
        var cacheKey = $"product:{id}";

        _memoryCache.Remove(cacheKey);
        await _distributedCache.RemoveAsync(cacheKey, ct);

        _logger.LogInformation("Invalidated cache for {CacheKey}", cacheKey);
    }
}

// Stale-while-revalidate pattern
public class StaleWhileRevalidateCache<T>
{
    private readonly IDistributedCache _cache;
    private readonly TimeSpan _freshDuration;
    private readonly TimeSpan _staleDuration;

    public async Task<T?> GetOrCreateAsync(
        string key,
        Func<CancellationToken, Task<T>> factory,
        CancellationToken ct = default)
    {
        var cached = await _cache.GetStringAsync(key, ct);

        if (cached != null)
        {
            var entry = JsonSerializer.Deserialize<CacheEntry<T>>(cached)!;

            if (entry.IsStale && !entry.IsExpired)
            {
                // Return stale data immediately, refresh in background
                _ = Task.Run(async () =>
                {
                    var fresh = await factory(CancellationToken.None);
                    await SetAsync(key, fresh, CancellationToken.None);
                });
            }

            if (!entry.IsExpired)
                return entry.Value;
        }

        // Cache miss or expired
        var value = await factory(ct);
        await SetAsync(key, value, ct);
        return value;
    }

    private record CacheEntry<TValue>(TValue Value, DateTime CreatedAt)
    {
        public bool IsStale => DateTime.UtcNow - CreatedAt > _freshDuration;
        public bool IsExpired => DateTime.UtcNow - CreatedAt > _staleDuration;
    }
}
```

## Testing Patterns

### Unit Tests with xUnit and Moq

```csharp
public class OrderServiceTests
{
    private readonly Mock<IOrderRepository> _mockRepository;
    private readonly Mock<IStockService> _mockStockService;
    private readonly Mock<IValidator<CreateOrderRequest>> _mockValidator;
    private readonly OrderService _sut; // System Under Test

    public OrderServiceTests()
    {
        _mockRepository = new Mock<IOrderRepository>();
        _mockStockService = new Mock<IStockService>();
        _mockValidator = new Mock<IValidator<CreateOrderRequest>>();

        // Default: validation passes
        _mockValidator
            .Setup(v => v.ValidateAsync(It.IsAny<CreateOrderRequest>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ValidationResult());

        _sut = new OrderService(
            _mockRepository.Object,
            _mockStockService.Object,
            _mockValidator.Object);
    }

    [Fact]
    public async Task CreateOrderAsync_WithValidRequest_ReturnsSuccess()
    {
        // Arrange
        var request = new CreateOrderRequest
        {
            ProductId = "PROD-001",
            Quantity = 5,
            CustomerOrderCode = "ORD-2024-001"
        };

        _mockStockService
            .Setup(s => s.CheckAsync("PROD-001", 5, It.IsAny<CancellationToken>()))
            .ReturnsAsync(new StockResult { IsAvailable = true, Available = 10 });

        _mockRepository
            .Setup(r => r.CreateAsync(It.IsAny<Order>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new Order { Id = 1, CustomerOrderCode = "ORD-2024-001" });

        // Act
        var result = await _sut.CreateOrderAsync(request);

        // Assert
        Assert.True(result.IsSuccess);
        Assert.NotNull(result.Value);
        Assert.Equal(1, result.Value.Id);

        _mockRepository.Verify(
            r => r.CreateAsync(It.Is<Order>(o => o.CustomerOrderCode == "ORD-2024-001"),
            It.IsAny<CancellationToken>()),
            Times.Once);
    }

    [Fact]
    public async Task CreateOrderAsync_WithInsufficientStock_ReturnsFailure()
    {
        // Arrange
        var request = new CreateOrderRequest { ProductId = "PROD-001", Quantity = 100 };

        _mockStockService
            .Setup(s => s.CheckAsync(It.IsAny<string>(), It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new StockResult { IsAvailable = false, Available = 5 });

        // Act
        var result = await _sut.CreateOrderAsync(request);

        // Assert
        Assert.False(result.IsSuccess);
        Assert.Equal("INSUFFICIENT_STOCK", result.ErrorCode);
        Assert.Contains("5 available", result.Error);

        _mockRepository.Verify(
            r => r.CreateAsync(It.IsAny<Order>(), It.IsAny<CancellationToken>()),
            Times.Never);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    [InlineData(-100)]
    public async Task CreateOrderAsync_WithInvalidQuantity_ReturnsValidationError(int quantity)
    {
        // Arrange
        var request = new CreateOrderRequest { ProductId = "PROD-001", Quantity = quantity };

        _mockValidator
            .Setup(v => v.ValidateAsync(request, It.IsAny<CancellationToken>()))
            .ReturnsAsync(new ValidationResult(new[]
            {
                new ValidationFailure("Quantity", "Quantity must be greater than 0")
            }));

        // Act
        var result = await _sut.CreateOrderAsync(request);

        // Assert
        Assert.False(result.IsSuccess);
        Assert.Equal("VALIDATION_ERROR", result.ErrorCode);
    }
}
```

### Integration Tests with WebApplicationFactory

```csharp
public class ProductsApiTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;
    private readonly HttpClient _client;

    public ProductsApiTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory.WithWebHostBuilder(builder =>
        {
            builder.ConfigureServices(services =>
            {
                // Replace real database with in-memory
                services.RemoveAll<DbContextOptions<AppDbContext>>();
                services.AddDbContext<AppDbContext>(options =>
                    options.UseInMemoryDatabase("TestDb"));

                // Replace Redis with memory cache
                services.RemoveAll<IDistributedCache>();
                services.AddDistributedMemoryCache();
            });
        });

        _client = _factory.CreateClient();
    }

    [Fact]
    public async Task GetProduct_WithValidId_ReturnsProduct()
    {
        // Arrange
        using var scope = _factory.Services.CreateScope();
        var context = scope.ServiceProvider.GetRequiredService<AppDbContext>();

        context.Products.Add(new Product
        {
            Id = "TEST-001",
            Name = "Test Product",
            Price = 99.99m
        });
        await context.SaveChangesAsync();

        // Act
        var response = await _client.GetAsync("/api/products/TEST-001");

        // Assert
        response.EnsureSuccessStatusCode();
        var product = await response.Content.ReadFromJsonAsync<Product>();
        Assert.Equal("Test Product", product!.Name);
    }

    [Fact]
    public async Task GetProduct_WithInvalidId_Returns404()
    {
        // Act
        var response = await _client.GetAsync("/api/products/NONEXISTENT");

        // Assert
        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }
}
```

## Best Practices

### DO

1. **Use async/await** all the way through the call stack
2. **Inject dependencies** through constructor injection
3. **Use IOptions<T>** for typed configuration
4. **Return Result types** instead of throwing exceptions for business logic
5. **Use CancellationToken** in all async methods
6. **Prefer Dapper** for read-heavy, performance-critical queries
7. **Use EF Core** for complex domain models with change tracking
8. **Cache aggressively** with proper invalidation strategies
9. **Write unit tests** for business logic, integration tests for APIs
10. **Use record types** for DTOs and immutable data

### DON'T

1. **Don't block on async** with `.Result` or `.Wait()`
2. **Don't use async void** except for event handlers
3. **Don't catch generic Exception** without re-throwing or logging
4. **Don't hardcode** configuration values
5. **Don't expose EF entities** directly in APIs (use DTOs)
6. **Don't forget** `AsNoTracking()` for read-only queries
7. **Don't ignore** CancellationToken parameters
8. **Don't create** `new HttpClient()` manually (use IHttpClientFactory)
9. **Don't mix** sync and async code unnecessarily
10. **Don't skip** validation at API boundaries

## Common Pitfalls

- **N+1 Queries**: Use `.Include()` or explicit joins
- **Memory Leaks**: Dispose IDisposable resources, use `using`
- **Deadlocks**: Don't mix sync and async, use ConfigureAwait(false) in libraries
- **Over-fetching**: Select only needed columns, use projections
- **Missing Indexes**: Check query plans, add indexes for common filters
- **Timeout Issues**: Configure appropriate timeouts for HTTP clients
- **Cache Stampede**: Use distributed locks for cache population
